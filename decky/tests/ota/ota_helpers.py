"""Shared helpers for the OTA tests: signing fixtures, release builder, fake serial, fake GitHub."""
from __future__ import annotations

import hashlib
import json
import os
import random

from inkterface_core import proto as P
from inkterface_core.emulator import EmulatedPanel
from inkterface_core.ota import ed25519, github, manifest as M
from inkterface_core.usb_link import Link

FIX = os.path.join(os.path.dirname(__file__), "fixtures")
SEED = bytes.fromhex(open(os.path.join(FIX, "TEST-ONLY-signing.key")).read().strip())
PUB = ed25519.publickey(SEED)
OTHER_SEED = hashlib.sha256(b"some other key").digest()
SLOT = 0x330000


def make_image(n: int = 20_000, seed: int = 1) -> bytes:
    """Fake firmware: starts with the ESP image magic 0xE9, rest deterministic noise."""
    rnd = random.Random(seed)
    return b"\xe9" + bytes(rnd.getrandbits(8) for _ in range(n - 1))


def manifest_doc(image: bytes, version="1.2.0", **over) -> dict:
    d = {
        "version": version,
        "protocol_version": P.PROTO_VER,
        "sha256": hashlib.sha256(image).hexdigest(),
        "size": len(image),
        "min_plugin_version": "1.0.0",
        "changelog": "Faster refresh.",
    }
    d.update(over)
    return d


def sign_doc(doc: dict, seed: bytes = SEED) -> tuple[bytes, bytes]:
    raw = json.dumps(doc, indent=2).encode()
    return raw, ed25519.sign(seed, raw)


# --- fake GitHub ------------------------------------------------------------------
def dl_url(tag: str, name: str, repo: str = github.REPO) -> str:
    return f"https://github.com/{repo}/releases/download/{tag}/{name}"


def release_json(version: str, image: bytes, *, raw=None, sig=None, prerelease=False, draft=False, drop=(), size=None, urls=None):
    tag = f"fw-v{version}"
    names = ["manifest.json", "manifest.sig", M.asset_name(version)]
    assets = [{"name": n, "browser_download_url": (urls or {}).get(n, dl_url(tag, n)),
               "size": (size if n == M.asset_name(version) and size is not None else len(image))}
              for n in names if n not in drop]
    return {"tag_name": tag, "draft": draft, "prerelease": prerelease, "assets": assets,
            "html_url": f"https://github.com/{github.REPO}/releases/tag/{tag}"}


class FakeGitHub:
    """fetch(url, max_bytes) over a dict url -> Response | bytes | Exception. Records requests."""

    def __init__(self):
        self.routes: dict[str, object] = {}
        self.calls: list[tuple[str, int]] = []
        self.sent: list[dict] = []   # request headers of each call (empty dict if none)

    def publish(self, version: str, image: bytes, *, doc=None, seed=SEED, sig=None, raw=None, **rel_kw) -> dict:
        doc = doc or manifest_doc(image, version)
        if raw is None:
            raw, s = sign_doc(doc, seed)
            sig = s if sig is None else sig
        tag = f"fw-v{version}"
        self.routes[dl_url(tag, "manifest.json")] = raw
        self.routes[dl_url(tag, "manifest.sig")] = sig
        self.routes[dl_url(tag, M.asset_name(version))] = image
        return release_json(version, image, **rel_kw)

    def listing(self, releases, etag=None, last_modified=None):
        body = json.dumps(releases).encode()
        h = {k: v for k, v in (("etag", etag), ("last-modified", last_modified)) if v}
        self.routes[f"{github.API}/repos/{github.REPO}/releases?per_page=20"] = github.Response(200, body, h) if h else body

    def __call__(self, url: str, max_bytes: int, headers=None) -> github.Response:
        self.calls.append((url, max_bytes))
        self.sent.append(headers or {})
        r = self.routes.get(url, github.Response(404))
        if isinstance(r, Exception):
            raise r
        if isinstance(r, bytes):
            r = github.Response(200, r)
        et = r.headers.get("etag")
        if r.status == 200 and et and (headers or {}).get("If-None-Match") == et:   # what GitHub does: 304, no body
            r = github.Response(304, b"", r.headers)
        if len(r.body) > max_bytes:  # what urllib_fetch does
            from inkterface_core.ota.errors import TooLarge
            raise TooLarge("too big")
        return r


# --- fake serial in front of the emulator --------------------------------------------
class FakeSerial:
    """read(n, timeout)/write(b, timeout) like inkterface_core.serialport, backed by an EmulatedPanel.

    drop_after: after that many bytes written the link dies (the cut frame is half delivered).
    lose_write: set of write-call indexes (0-based) the panel never sees (lost on the wire).
    mute: panel never answers (mute_after: ... from write N on).
    """

    def __init__(self, panel: EmulatedPanel, drop_after=None, lose_write=(), mute=False, mute_after=None):
        self.panel, self.drop_after, self.lose, self.mute = panel, drop_after, set(lose_write), mute
        self.mute_after = mute_after  # go silent once this many writes were made
        panel.flush_stale()  # a reconnect is >100 ms later: the panel dropped any half frame
        self.rx = bytearray()
        self.written = 0
        self.writes = 0
        self.dead = False
        self.closed = False

    def write(self, data: bytes, timeout=None) -> None:
        if self.dead:
            raise OSError("link is down")
        i, self.writes = self.writes, self.writes + 1
        if self.mute_after is not None and i >= self.mute_after:
            self.mute = True
        if self.drop_after is not None and self.written + len(data) > self.drop_after:
            k = max(0, self.drop_after - self.written)
            self.written += k
            self._deliver(data[:k])
            self.dead = True
            raise OSError("link dropped")
        self.written += len(data)
        if i not in self.lose:
            self._deliver(data)

    def _deliver(self, data: bytes) -> None:
        out = self.panel.feed(data)
        if not self.mute:
            self.rx += out

    @property
    def in_waiting(self) -> int:
        if self.dead:
            raise OSError("link is down")
        return len(self.rx)

    def read(self, n: int = 4096, timeout=None) -> bytes:
        if self.dead:
            raise OSError("link is down")
        out = bytes(self.rx[:n])
        del self.rx[:n]
        return out

    def close(self) -> None:
        self.closed = True


class Bench:
    """One emulated panel + a way to (re)open it like the plugin does. Reboots after OTA_END."""

    def __init__(self, panel=None, **serial_kw):
        self.panel = panel or EmulatedPanel(fw_ver="1.0.0", ota_slot=SLOT)
        self.serial_kw = serial_kw  # applies to the next open only
        self.handshake_timeout = 0.5
        self.opens: list[FakeSerial] = []
        self.opens_kw: list[dict] = []

    def next_open(self, **kw):
        self.opens_kw.append(kw)

    def open_panel(self):
        from inkterface_core.ota import stream

        if self.panel.ota_committed:  # END was accepted: the panel resets before we reconnect
            self.panel.reboot()
        kw = self.opens_kw.pop(0) if self.opens_kw else {}
        ser = FakeSerial(self.panel, **kw)
        self.opens.append(ser)
        link = Link(ser)
        return link, stream.handshake(link, timeout=self.handshake_timeout)
