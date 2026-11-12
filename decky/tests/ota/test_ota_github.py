import io
import ssl
import urllib.error

import pytest
from ota_helpers import OTHER_SEED, PUB, FakeGitHub, dl_url, make_image, manifest_doc, sign_doc

from inkterface_core import proto as P
from inkterface_core.ota import errors as E
from inkterface_core.ota import github as G
from inkterface_core.ota import manifest as M

IMG = make_image(30_000)
LIST_URL = f"{G.API}/repos/{G.REPO}/releases?per_page=20"


def find(gh, installed="1.0.0", plugin="1.0.0", key=PUB, **kw):
    return G.find_release(gh, key, plugin_version=plugin, installed_fw=installed, **kw)


@pytest.fixture
def gh():
    return FakeGitHub()


def test_finds_newest_fw_release_ignores_plugin_draft_prerelease(gh):
    old, new = make_image(5000, 2), make_image(6000, 3)
    gh.listing([
        {"tag_name": "plugin-v9.9.9", "assets": []},                       # plugin release: not firmware
        gh.publish("1.1.0", old),
        gh.publish("1.3.0", IMG, draft=True),                              # draft: invisible
        gh.publish("1.4.0-rc.1", IMG, prerelease=True),                    # prerelease: skipped by default
        gh.publish("1.2.0", new),
        {"tag_name": "fw-vgarbage", "assets": []},
        "not a dict", None,
    ])
    r = find(gh)
    assert r.tag == "fw-v1.2.0" and r.manifest.version == "1.2.0"
    assert r.bin_url == dl_url("fw-v1.2.0", "inkterface-ee05-1.2.0.bin")
    assert r.page_url.endswith("/tag/fw-v1.2.0")
    assert gh.calls[0] == (LIST_URL, G.MAX_LISTING)


def test_prerelease_opt_in(gh):
    gh.listing([gh.publish("1.2.0", IMG), gh.publish("1.3.0-rc.1", IMG, prerelease=True)])
    assert find(gh, include_prerelease=True).tag == "fw-v1.3.0-rc.1"


def test_no_firmware_release_or_empty_list_is_none(gh):
    gh.listing([])
    assert find(gh) is None
    gh.listing([{"tag_name": "plugin-v1.0.0", "assets": []}])
    assert find(gh) is None


def test_up_to_date_is_none_and_dev_build_gets_offer(gh):
    gh.listing([gh.publish("1.2.0", IMG)])
    assert find(gh, installed="1.2.0") is None
    assert find(gh, installed="gABC-DIRTY").tag == "fw-v1.2.0"
    assert find(gh, installed="").tag == "fw-v1.2.0"


def test_downgrade_refused(gh):
    gh.listing([gh.publish("1.1.0", IMG)])
    with pytest.raises(E.Downgrade):
        find(gh, installed="1.2.0")


def test_protocol_and_plugin_version_checks_reach_find(gh):
    gh.listing([gh.publish("1.2.0", IMG, doc=manifest_doc(IMG, "1.2.0", protocol_version=P.PROTO_VER + 1))])
    with pytest.raises(E.ProtocolMismatch):
        find(gh)
    gh.listing([gh.publish("1.2.0", IMG, doc=manifest_doc(IMG, "1.2.0", min_plugin_version="3.0.0"))])
    with pytest.raises(E.PluginTooOld):
        find(gh)


def test_unsigned_wrong_key_and_no_key(gh):
    gh.listing([gh.publish("1.2.0", IMG, seed=OTHER_SEED)])
    with pytest.raises(E.BadSignature):
        find(gh)
    gh.listing([gh.publish("1.2.0", IMG, sig=b"\0" * 64)])
    with pytest.raises(E.BadSignature):
        find(gh)
    gh.listing([gh.publish("1.2.0", IMG)])
    with pytest.raises(E.NoTrustKey):
        find(gh, key=None)


def test_tampered_manifest_bytes(gh):
    rel = gh.publish("1.2.0", IMG)
    gh.listing([rel])
    url = dl_url("fw-v1.2.0", "manifest.json")
    gh.routes[url] = gh.routes[url].replace(b"Faster", b"Slower")
    with pytest.raises(E.BadSignature):
        find(gh)


def test_manifest_for_other_version_than_tag(gh):
    # attacker re-attaches a valid signed manifest of 1.0.5 to the fw-v1.2.0 release
    other = make_image(4000, 9)
    raw, sig = sign_doc(manifest_doc(other, "1.0.5"))
    gh.listing([gh.publish("1.2.0", IMG, raw=raw, sig=sig)])
    with pytest.raises(E.BadManifest, match="tag"):
        find(gh)


def test_asset_size_differs_from_signed_size(gh):
    gh.listing([gh.publish("1.2.0", IMG, size=len(IMG) + 1)])
    with pytest.raises(E.BadManifest, match="size"):
        find(gh)


def test_missing_assets(gh):
    for name in ("manifest.json", "manifest.sig", "inkterface-ee05-1.2.0.bin"):
        gh.listing([gh.publish("1.2.0", IMG, drop=(name,))])
        with pytest.raises(E.BadResponse, match=name):
            find(gh)


def test_unexpected_download_urls_rejected(gh):
    for bad in ("file:///etc/passwd", "https://evil.example/manifest.json", "http://github.com/x", None):
        gh.listing([gh.publish("1.2.0", IMG, urls={"manifest.json": bad})])
        with pytest.raises(E.BadResponse, match="URL"):
            find(gh)


def test_malformed_listing(gh):
    for body in (b"not json", b'{"a": 1}', b'"x"'):
        gh.routes[LIST_URL] = body
        with pytest.raises(E.BadResponse):
            find(gh)
    rel = gh.publish("1.2.0", IMG)
    rel["assets"] = "nope"
    gh.listing([rel])
    with pytest.raises(E.BadResponse):
        find(gh)


def test_api_errors(gh):
    gh.routes[LIST_URL] = G.Response(404)
    with pytest.raises(E.BadResponse, match="404"):
        find(gh)
    gh.routes[LIST_URL] = G.Response(503)
    with pytest.raises(E.NetworkError, match="503"):
        find(gh)
    gh.routes[LIST_URL] = G.Response(418)
    with pytest.raises(E.BadResponse, match="418"):
        find(gh)
    gh.routes[LIST_URL] = G.Response(403)  # plain 403, not a rate limit
    with pytest.raises(E.BadResponse):
        find(gh)
    gh.routes[LIST_URL] = E.NetworkError("offline")
    with pytest.raises(E.NetworkError):
        find(gh)


def test_rate_limit_variants(gh):
    gh.routes[LIST_URL] = G.Response(403, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1893456000"})
    with pytest.raises(E.RateLimited) as e:
        find(gh)
    assert e.value.retry_at == 1893456000.0
    gh.routes[LIST_URL] = G.Response(429, headers={"retry-after": "60"})
    with pytest.raises(E.RateLimited) as e:
        G._get(gh, LIST_URL, 10, "x")
    assert e.value.retry_at is None and e.value.retry_after == 60.0
    gh.routes[LIST_URL] = G.Response(429, headers={})
    with pytest.raises(E.RateLimited) as e:
        find(gh)
    assert e.value.retry_at is None
    gh.routes[LIST_URL] = G.Response(403, headers={"retry-after": "x"})
    with pytest.raises(E.RateLimited):
        find(gh)


def test_oversize_manifest_download(gh):
    gh.listing([gh.publish("1.2.0", IMG)])
    gh.routes[dl_url("fw-v1.2.0", "manifest.json")] = b"x" * (M.MAX_MANIFEST + 1)
    with pytest.raises(E.TooLarge):
        find(gh)


# --- download ------------------------------------------------------------------------
def release(gh, image=IMG):
    gh.listing([gh.publish("1.2.0", image)])
    return find(gh)


def test_download_ok_and_cap(gh):
    r = release(gh)
    assert G.download_image(gh, r) == IMG
    assert gh.calls[-1][1] == len(IMG)  # fetch was told the size cap


def test_download_oversize_hash_and_short(gh):
    r = release(gh)
    url = r.bin_url
    gh.routes[url] = IMG + b"\0"
    with pytest.raises(E.TooLarge):
        G.download_image(gh, r)
    gh.routes[url] = IMG[:-1]
    with pytest.raises(E.HashMismatch, match="bytes"):
        G.download_image(gh, r)
    bad = bytearray(IMG)
    bad[1000] ^= 1
    gh.routes[url] = bytes(bad)
    with pytest.raises(E.HashMismatch, match="sha256"):
        G.download_image(gh, r)
    gh.routes[url] = G.Response(404)
    with pytest.raises(E.BadResponse):
        G.download_image(gh, r)


# --- urllib_fetch (urlopen monkeypatched) --------------------------------------------------
class FakeHTTP(io.BytesIO):
    def __init__(self, body, status=200, headers=None):
        super().__init__(body)
        self.status, self.headers = status, headers or {"X-Test": "1"}


def patch_urlopen(monkeypatch, result):
    seen = {}

    def fake(req, timeout, context):
        seen["req"], seen["timeout"], seen["context"] = req, timeout, context
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(G.urllib.request, "urlopen", fake)
    return seen


def test_urllib_fetch_ok(monkeypatch):
    seen = patch_urlopen(monkeypatch, FakeHTTP(b"hello"))
    r = G.urllib_fetch("https://api.github.com/x", 100)
    assert (r.status, r.body, r.headers) == (200, b"hello", {"x-test": "1"})
    assert seen["req"].get_header("User-agent") == G.USER_AGENT
    assert seen["req"].get_header("Authorization") is None
    assert seen["context"].verify_mode == ssl.CERT_REQUIRED and seen["context"].check_hostname


def test_urllib_fetch_cap_http_error_and_network(monkeypatch):
    patch_urlopen(monkeypatch, FakeHTTP(b"x" * 11))
    with pytest.raises(E.TooLarge):
        G.urllib_fetch("https://h/x", 10)
    err = urllib.error.HTTPError("https://h/x", 404, "nf", {"Retry-After": "5"}, io.BytesIO(b"nope"))
    patch_urlopen(monkeypatch, err)
    r = G.urllib_fetch("https://h/x", 10)
    assert (r.status, r.body, r.headers["retry-after"]) == (404, b"nope", "5")
    for exc in (urllib.error.URLError("dns"), TimeoutError("slow"), ConnectionResetError("rst")):
        patch_urlopen(monkeypatch, exc)
        with pytest.raises(E.NetworkError):
            G.urllib_fetch("https://h/x", 10)


def test_urllib_fetch_refuses_non_https():
    for url in ("http://h/x", "file:///etc/passwd", "ftp://h/x"):
        with pytest.raises(E.NetworkError):
            G.urllib_fetch(url, 10)

