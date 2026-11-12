"""Find, verify and download firmware releases from GitHub. Stdlib only.

All network access goes through ONE injectable callable, `fetch(url, max_bytes) -> Response`
(`urllib_fetch` by default), so tests never touch the network. The repo is public: no token.

Flow (docs/ota.md): list releases -> newest `fw-v<semver>` -> manifest.json + manifest.sig ->
verify signature -> schema -> policy (protocol, min plugin, no downgrade) -> [user taps
Install] -> `download_image` (size cap + sha256) -> usb streamer.

Rate limit (60/h per public IP, shared by everyone behind it, Decky Loader and other plugins included): every API
request counts, a conditional one answered 304 too (that is only free with a token). So the listing is cached on disk
(`ReleaseCache`, with ETag / Last-Modified and when it was fetched) and an automatic check younger than `fresh_s` makes no
request at all, which keeps plugin restarts off the API. When the API says no, the cached listing - or for a stable
plugin the github.com `releases/latest/download/` URLs, which are not API-limited - stands in. Everything that comes out
of any path goes through the same signature check.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .. import tls
from . import manifest as M
from .errors import BadManifest, BadResponse, HashMismatch, NetworkError, RateLimited, TooLarge

REPO = "FliesWithWind/inkterface"
API = "https://api.github.com"
TAG_PREFIX = "fw-v"
USER_AGENT = "inkterface-decky-plugin"
MAX_LISTING = 4 * 1024 * 1024
MAX_SIG = 256
LIST_PER_PAGE = 20   # shortcut: the newest fw-v release must be among the 20 newest releases; raise if plugin releases ever crowd it out
MAX_CACHE = 256 * 1024
MAX_CACHED_RELEASES = 50
log = logging.getLogger(__name__)
MANIFEST_ASSET, SIG_ASSET = "manifest.json", "manifest.sig"


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes = b""
    headers: dict = field(default_factory=dict)  # lowercase header names


Fetch = Callable[[str, int], Response]


def urllib_fetch(url: str, max_bytes: int, headers: dict | None = None, timeout: float = 20.0) -> Response:
    """GET `url` (+ extra request `headers`, e.g. If-None-Match). Non-2xx statuses (304 included) are returned, not
    raised. Raises NetworkError / TooLarge."""
    if not url.startswith("https://"):
        raise NetworkError(f"refusing non-https URL {url!r}")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json, */*",
                                               **(headers or {})})
    try:
        try:
            r = urllib.request.urlopen(req, timeout=timeout, context=tls.make_ssl_context())
        except urllib.error.HTTPError as e:
            r = e
        with r:
            body = r.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise TooLarge(f"response from {url} exceeds {max_bytes} bytes")
            return Response(r.status, body, {k.lower(): v for k, v in r.headers.items()})
    except (urllib.error.URLError, OSError, ValueError) as e:  # TimeoutError, http.client errors, ssl
        raise NetworkError(f"cannot reach GitHub: {tls.describe(e)}") from None


def _num(h: dict, name: str) -> float | None:
    try:
        return float(h[name]) if name in h else None
    except ValueError:
        return None


def _request(fetch: Fetch, url: str, max_bytes: int, what: str, headers: dict | None = None, ok: tuple = (200,)) -> Response:
    # only pass `headers` when there are some: a plain fetch(url, max_bytes) fake keeps working
    r = fetch(url, max_bytes, headers) if headers else fetch(url, max_bytes)
    if r.status in ok:
        return r
    h = r.headers
    if r.status == 429 or (r.status == 403 and (h.get("x-ratelimit-remaining") == "0" or "retry-after" in h)):
        raise RateLimited(f"GitHub rate limit hit while fetching {what}", _num(h, "x-ratelimit-reset"), _num(h, "retry-after"))
    if r.status == 404:
        raise BadResponse(f"{what} not found (HTTP 404)")
    if r.status >= 500:
        raise NetworkError(f"GitHub is unavailable (HTTP {r.status}) while fetching {what}")
    raise BadResponse(f"unexpected HTTP {r.status} while fetching {what}")


def _get(fetch: Fetch, url: str, max_bytes: int, what: str) -> bytes:
    return _request(fetch, url, max_bytes, what).body


# -- listing cache ------------------------------------------------------------------
_HDR = re.compile(r"[\x20-\x7e]{1,256}")   # a validator that is echoed into a request header: printable ASCII only


def _slim(rel: dict) -> dict:
    """Only what find_release reads: keeps the cache small whatever else GitHub adds to a release."""
    a = rel.get("assets")
    return {"tag_name": rel["tag_name"], "draft": rel.get("draft"), "prerelease": rel.get("prerelease"),
            "html_url": rel.get("html_url"),
            "assets": [{k: x.get(k) for k in ("name", "browser_download_url", "size")} for x in a if isinstance(x, dict)]
            if isinstance(a, list) else None}


class ReleaseCache:
    """The last release listing GitHub sent, with its ETag / Last-Modified, in one small JSON file.

    Best effort: a missing, oversized, unreadable or malformed file is ignored (never raised), a failed write is
    logged. `path=None` keeps it in memory only. `limited` is set by `find_release` to the RateLimited it absorbed.
    """

    def __init__(self, path: str | os.PathLike | None = None):
        self.path = Path(path) if path else None
        self.limited: RateLimited | None = None
        self.checked_at: float | None = None   # when the listing in use was fetched from the API (set by find_release)
        self._mem: dict | None = None

    def load(self, url: str) -> dict | None:
        """{'etag', 'last_modified', 'releases', 'checked_at'} for `url`, or None."""
        if self.path is None:
            d = self._mem
        else:
            try:
                with open(self.path, "rb") as f:
                    raw = f.read(MAX_CACHE + 1)
                d = json.loads(raw) if len(raw) <= MAX_CACHE else None
            except (OSError, ValueError):
                d = None
        try:
            if (isinstance(d, dict) and d.get("v") == 1 and d.get("url") == url
                    and all(d.get(k) is None or (isinstance(d[k], str) and _HDR.fullmatch(d[k])) for k in ("etag", "last_modified"))
                    and (d.get("etag") or d.get("last_modified"))):
                rels = d.get("releases")
                if (isinstance(rels, list) and len(rels) <= MAX_CACHED_RELEASES
                        and all(isinstance(r, dict) and isinstance(r.get("tag_name"), str) for r in rels)):
                    at = d.get("checked_at")
                    return {"etag": d.get("etag"), "last_modified": d.get("last_modified"), "releases": rels,
                            "checked_at": float(at) if isinstance(at, (int, float)) and not isinstance(at, bool) else 0.0}
        except TypeError:
            pass
        return None

    def save(self, url: str, etag: str | None, last_modified: str | None, listing: list, checked_at: float = 0.0) -> None:
        rels = [_slim(r) for r in listing if isinstance(r, dict) and isinstance(r.get("tag_name"), str)
                and r["tag_name"].startswith(TAG_PREFIX)][:MAX_CACHED_RELEASES]
        d = {"v": 1, "url": url, "etag": etag, "last_modified": last_modified, "checked_at": checked_at,
             "releases": rels}
        self._mem = d
        if self.path is None:
            return
        try:
            os.makedirs(self.path.parent, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".fwrel-", suffix=".tmp", dir=self.path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(d, f)
                os.replace(tmp, self.path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        except OSError as e:
            log.warning("cannot write the release cache %s: %s", self.path, e)


def _listing(fetch: Fetch, repo: str, cache: ReleaseCache | None, fresh_s: float, now: float) -> list:
    """The release list: the cache if it is younger than `fresh_s` (no request), else a conditional request (the cached
    list on 304). May raise RateLimited."""
    url = f"{API}/repos/{repo}/releases?per_page={LIST_PER_PAGE}"
    c = cache.load(url) if cache else None
    if c and 0 <= now - c["checked_at"] < fresh_s:   # a clock that went backwards is never "fresh"
        cache.checked_at = c["checked_at"]
        return c["releases"]
    cond = {}
    if c:
        if c["etag"]:
            cond["If-None-Match"] = c["etag"]
        if c["last_modified"]:
            cond["If-Modified-Since"] = c["last_modified"]
    r = _request(fetch, url, MAX_LISTING, "the release list", cond, (200, 304))
    if r.status == 304:
        if c is None:
            raise BadResponse("GitHub answered 'not modified' to an unconditional request")
        cache.checked_at = now
        cache.save(url, c["etag"], c["last_modified"], c["releases"], now)
        return c["releases"]
    try:
        listing = json.loads(r.body)
    except ValueError:
        raise BadResponse("release list is not valid JSON") from None
    if not isinstance(listing, list):
        raise BadResponse("release list has an unexpected shape")
    etag, lm = r.headers.get("etag"), r.headers.get("last-modified")
    if cache:
        cache.checked_at = now
        if etag or lm:
            cache.save(url, etag, lm, listing, now)
    return listing


@dataclass(frozen=True)
class Release:
    tag: str
    manifest: M.Manifest
    bin_url: str
    page_url: str = ""


def _download_url(repo: str, tag: str, name: str, a: dict) -> str:
    url = a.get("browser_download_url")
    want = f"https://github.com/{repo}/releases/download/{tag}/{name}"
    if url != want:  # also blocks file:// and foreign hosts smuggled in via the JSON
        raise BadResponse(f"asset {name} of {tag} has an unexpected download URL")
    return url


def find_release(
    fetch: Fetch,
    public_key: bytes | None,
    *,
    plugin_version: str,
    installed_fw: str = "",
    repo: str = REPO,
    include_prerelease: bool = False,
    cache: ReleaseCache | None = None,
    fresh_s: float = 0.0,
    now: float | None = None,
) -> Release | None:
    """Newest verified firmware release the plugin may offer, or None (up to date / none published).

    Only the newest `fw-v*` release is examined: if it is unsigned or broken this raises rather
    than silently offering an older one. Raises the OtaError subclasses from `errors`.

    API rate-limited: the cached listing is used if there is one; else (stable plugins only) the newest
    github.com release's manifest; else RateLimited. Either way `cache.limited` carries the limit.
    `fresh_s` > 0: a cached listing fetched less than that long ago (before `now`) is used without any request.
    """
    if cache:
        cache.limited = cache.checked_at = None
    try:
        listing = _listing(fetch, repo, cache, fresh_s, time.time() if now is None else now)
    except RateLimited as e:
        if cache:
            cache.limited = e
        stale = cache.load(f"{API}/repos/{repo}/releases?per_page={LIST_PER_PAGE}") if cache else None
        if stale is not None:
            listing = stale["releases"]
        elif include_prerelease:   # releases/latest never points at a pre-release
            raise
        else:
            try:
                return _from_latest(fetch, public_key, plugin_version, installed_fw, repo)
            except (BadResponse, NetworkError):   # e.g. the newest release is a plugin one: report the limit
                raise e from None

    best = None
    for rel in listing:
        if not isinstance(rel, dict) or rel.get("draft") or (rel.get("prerelease") and not include_prerelease):
            continue
        tag = rel.get("tag_name")
        if not isinstance(tag, str) or not tag.startswith(TAG_PREFIX):
            continue
        try:
            key = M.semver(tag[len(TAG_PREFIX):])
        except ValueError:
            continue
        if best is None or key > best[0]:
            best = (key, tag, rel)
    if best is None:
        return None
    _, tag, rel = best
    version = tag[len(TAG_PREFIX):]

    assets = rel.get("assets")
    if not isinstance(assets, list):
        raise BadResponse(f"release {tag} has no asset list")
    by_name = {a.get("name"): a for a in assets if isinstance(a, dict)}
    need = (MANIFEST_ASSET, SIG_ASSET, M.asset_name(version))
    for n in need:
        if n not in by_name:
            raise BadResponse(f"release {tag} is missing {n}")
    urls = {n: _download_url(repo, tag, n, by_name[n]) for n in need}

    mraw = _get(fetch, urls[MANIFEST_ASSET], M.MAX_MANIFEST, MANIFEST_ASSET)
    sig = _get(fetch, urls[SIG_ASSET], MAX_SIG, SIG_ASSET)
    m = M.verify_and_parse(mraw, sig, public_key)
    if m.version != version:
        raise BadManifest(f"manifest is for {m.version} but the release tag is {tag}")
    listed = by_name[m.asset].get("size")
    if isinstance(listed, int) and listed != m.size:
        raise BadManifest(f"asset size {listed} differs from the signed size {m.size}")

    installed = M.installed_version(installed_fw) if installed_fw else None
    if not M.check_policy(m, plugin_version=plugin_version, installed=installed):
        return None
    page = rel.get("html_url")
    return Release(tag, m, urls[m.asset], page if isinstance(page, str) else "")


def _from_latest(fetch: Fetch, public_key: bytes | None, plugin_version: str, installed_fw: str, repo: str) -> Release | None:
    """No API: github.com/<repo>/releases/latest/download/{manifest.json,manifest.sig} (not API-rate-limited).

    Same trust as the listing path: the signature is verified before the manifest is read, and the tag and download
    URL are derived from the SIGNED version. shortcut: "latest" is the newest non-pre-release of ANY kind, so this only
    works while that is a firmware release (a plugin release newer than it makes it 404 -> caller reports the limit).
    """
    base = f"https://github.com/{repo}/releases/latest/download/"
    mraw = _get(fetch, base + MANIFEST_ASSET, M.MAX_MANIFEST, MANIFEST_ASSET)
    sig = _get(fetch, base + SIG_ASSET, MAX_SIG, SIG_ASSET)
    m = M.verify_and_parse(mraw, sig, public_key)
    installed = M.installed_version(installed_fw) if installed_fw else None
    if not M.check_policy(m, plugin_version=plugin_version, installed=installed):
        return None
    tag = TAG_PREFIX + m.version
    return Release(tag, m, f"https://github.com/{repo}/releases/download/{tag}/{m.asset}",
                   f"https://github.com/{repo}/releases/tag/{tag}")


def download_image(fetch: Fetch, release: Release) -> bytes:
    """Fetch the .bin; exactly manifest.size bytes with the manifest sha256, else HashMismatch."""
    m = release.manifest
    data = _get(fetch, release.bin_url, m.size, m.asset)
    if len(data) != m.size:
        raise HashMismatch(f"{m.asset} is {len(data)} bytes, manifest says {m.size}")
    if hashlib.sha256(data).hexdigest() != m.sha256:
        raise HashMismatch(f"{m.asset} sha256 does not match the signed manifest")
    return data
