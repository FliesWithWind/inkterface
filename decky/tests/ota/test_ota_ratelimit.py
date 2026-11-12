"""GitHub API rate limit handling: conditional requests + on-disk cache, Retry-After, and the paths that avoid the API.

Signature gating must be identical on every path: each fallback below also has a tamper / wrong-key case.
"""
import io
import json

import pytest
from ota_helpers import OTHER_SEED, PUB, FakeGitHub, dl_url, make_image, manifest_doc, sign_doc

from inkterface_core.ota import errors as E
from inkterface_core.ota import github as G

IMG = make_image(30_000)
LIST_URL = f"{G.API}/repos/{G.REPO}/releases?per_page=20"
LATEST = f"https://github.com/{G.REPO}/releases/latest/download/"
LIMITED = G.Response(403, b"", {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1893456000"})


def find(gh, cache=None, **kw):
    return G.find_release(gh, PUB, plugin_version="1.0.0", installed_fw="1.0.0", cache=cache, **kw)


@pytest.fixture
def gh():
    return FakeGitHub()


@pytest.fixture
def cache(tmp_path):
    return G.ReleaseCache(tmp_path / "cache" / "fw-releases.json")


def sent(gh):
    """Request headers of each listing request, in order."""
    return [h for (u, _), h in zip(gh.calls, gh.sent) if u == LIST_URL]


def cache_doc(cache):
    return json.loads(cache.path.read_text())


# --- conditional requests ---------------------------------------------------------------
def test_asks_for_20_not_50(gh):
    gh.listing([gh.publish("1.2.0", IMG)])
    find(gh)
    assert gh.calls[0][0].endswith("releases?per_page=20")


def test_etag_is_stored_then_sent_and_304_uses_the_cached_list(gh, cache):
    gh.listing([gh.publish("1.2.0", IMG)], etag='"abc"', last_modified="Wed, 21 Oct 2026 07:28:00 GMT")
    assert find(gh, cache).tag == "fw-v1.2.0"
    assert sent(gh) == [{}]   # nothing cached yet: unconditional
    d = cache_doc(cache)
    assert d["etag"] == '"abc"' and d["last_modified"] == "Wed, 21 Oct 2026 07:28:00 GMT" and d["url"] == LIST_URL

    r = find(gh, cache)       # a fresh cache object (plugin restart) reads the file
    assert sent(gh)[1] == {"If-None-Match": '"abc"', "If-Modified-Since": "Wed, 21 Oct 2026 07:28:00 GMT"}
    assert r.tag == "fw-v1.2.0" and r.manifest.version == "1.2.0"   # list came from the cache (the fake answered 304 without a body)
    assert cache.limited is None


def fresh(gh, cache, now, fresh_s=100.0):
    return G.find_release(gh, PUB, plugin_version="1.0.0", installed_fw="1.0.0", cache=cache, fresh_s=fresh_s, now=now)


def test_a_fresh_cache_makes_no_listing_request_and_304_renews_it(gh, cache):
    gh.listing([gh.publish("1.2.0", IMG)], etag='"a"')
    fresh(gh, cache, 1000.0)                                 # nothing cached: asks
    assert len(sent(gh)) == 1 and cache_doc(cache)["checked_at"] == 1000.0
    assert fresh(gh, cache, 1099.0).tag == "fw-v1.2.0"       # 99 s old: no request
    assert len(sent(gh)) == 1 and cache.checked_at == 1000.0
    fresh(gh, cache, 1100.0)                                 # 100 s old: asks, 304
    assert sent(gh)[1] == {"If-None-Match": '"a"'} and cache.checked_at == 1100.0
    assert cache_doc(cache)["checked_at"] == 1100.0 and cache_doc(cache)["releases"][0]["tag_name"] == "fw-v1.2.0"


def test_a_clock_that_went_backwards_or_an_old_cache_file_is_not_fresh(gh, cache):
    gh.listing([gh.publish("1.2.0", IMG)], etag='"a"')
    fresh(gh, cache, 1000.0)
    fresh(gh, cache, 900.0)                                  # now < checked_at
    assert len(sent(gh)) == 2
    d = cache_doc(cache)
    for bad in (None, True, "1000", -5):                     # file from before checked_at existed, or damaged
        cache.path.write_text(json.dumps({**d, "checked_at": bad}))
        n = len(sent(gh))
        fresh(gh, G.ReleaseCache(cache.path), 1000.0)
        assert len(sent(gh)) == n + 1


def test_200_after_a_change_replaces_the_cache(gh, cache):
    gh.listing([gh.publish("1.2.0", IMG)], etag='"a"')
    find(gh, cache)
    gh.listing([gh.publish("1.3.0", make_image(9000, 5))], etag='"b"')
    assert find(gh, cache).tag == "fw-v1.3.0"
    assert cache_doc(cache)["etag"] == '"b"' and cache_doc(cache)["releases"][0]["tag_name"] == "fw-v1.3.0"


def test_no_validators_means_nothing_cached(gh, cache):
    gh.listing([gh.publish("1.2.0", IMG)])
    find(gh, cache)
    assert not cache.path.exists()


def test_304_to_an_unconditional_request_is_an_error(gh, cache):
    gh.routes[LIST_URL] = G.Response(304)
    with pytest.raises(E.BadResponse, match="not modified"):
        find(gh, cache)


def test_cache_is_bounded_and_keeps_only_firmware_releases(gh, cache):
    junk = [{"tag_name": f"plugin-v1.0.{i}", "body": "x" * 5000, "assets": []} for i in range(40)]
    fat = gh.publish("1.2.0", IMG)
    fat["body"] = "y" * 100_000
    fat["assets"][0]["uploader"] = {"blob": "z" * 50_000}
    gh.listing(junk + [fat], etag='"a"')
    find(gh, cache)
    assert cache.path.stat().st_size < 4096
    rels = cache_doc(cache)["releases"]
    assert [r["tag_name"] for r in rels] == ["fw-v1.2.0"]
    assert set(rels[0]["assets"][0]) == {"name", "browser_download_url", "size"}


def test_corrupt_cache_is_ignored_and_replaced(gh, cache):
    gh.listing([gh.publish("1.2.0", IMG)], etag='"good"')
    find(gh, cache)
    good = cache.path.read_text()
    d = json.loads(good)
    bad = [
        b"", b"not json", b"\xff\xfe\x00", b"[]", b'"x"', b"null",
        json.dumps({**d, "v": 2}).encode(),
        json.dumps({**d, "url": "https://api.github.com/repos/other/repo/releases?per_page=20"}).encode(),
        json.dumps({**d, "etag": 5}).encode(),
        json.dumps({**d, "etag": '"a"\r\nX-Evil: 1'}).encode(),   # would be a header injection
        json.dumps({**d, "etag": None, "last_modified": None}).encode(),
        json.dumps({**d, "releases": "nope"}).encode(),
        json.dumps({**d, "releases": [1, 2]}).encode(),
        json.dumps({**d, "releases": [{"tag_name": 7}]}).encode(),
        json.dumps({**d, "releases": [d["releases"][0]] * 51}).encode(),
        b" " * (G.MAX_CACHE + 1),
    ]
    for raw in bad:
        cache.path.write_bytes(raw)
        gh.calls.clear(), gh.sent.clear()
        assert find(gh, G.ReleaseCache(cache.path)).tag == "fw-v1.2.0"
        assert sent(gh) == [{}], raw[:40]                                 # no validator taken from a bad cache
        assert json.loads(cache.path.read_text())["etag"] == '"good"'     # and the 200 rewrote it


def test_unwritable_cache_does_not_break_the_check(gh, tmp_path):
    (tmp_path / "file").write_text("x")
    gh.listing([gh.publish("1.2.0", IMG)], etag='"a"')
    assert find(gh, G.ReleaseCache(tmp_path / "file" / "sub" / "c.json")).tag == "fw-v1.2.0"


def test_memory_only_cache(gh):
    c = G.ReleaseCache()
    gh.listing([gh.publish("1.2.0", IMG)], etag='"a"')
    find(gh, c)
    assert find(gh, c).tag == "fw-v1.2.0" and sent(gh)[1] == {"If-None-Match": '"a"'}


def test_urllib_fetch_sends_extra_headers(monkeypatch):
    seen = {}

    class R(io.BytesIO):
        status, headers = 304, {"ETag": '"a"'}

    monkeypatch.setattr(G.urllib.request, "urlopen", lambda req, timeout, context: seen.update(req=req) or R(b""))
    r = G.urllib_fetch("https://api.github.com/x", 100, {"If-None-Match": '"a"'})
    assert r.status == 304 and seen["req"].get_header("If-none-match") == '"a"'
    assert seen["req"].get_header("User-agent") == G.USER_AGENT


# --- rate limited: cache stands in ------------------------------------------------------------
def test_rate_limited_with_a_cache_uses_it_and_reports_the_limit(gh, cache):
    gh.listing([gh.publish("1.2.0", IMG)], etag='"a"')
    find(gh, cache)
    gh.routes[LIST_URL] = LIMITED
    r = find(gh, cache)
    assert r.tag == "fw-v1.2.0"
    assert isinstance(cache.limited, E.RateLimited) and cache.limited.retry_at == 1893456000.0
    assert sent(gh)[1] == {"If-None-Match": '"a"'}   # tried conditionally first
    gh.listing([gh.publish("1.2.0", IMG)], etag='"a"')
    find(gh, cache)
    assert cache.limited is None   # reset on the next call


def test_cached_path_still_verifies_the_signature(gh, cache):
    gh.listing([gh.publish("1.2.0", IMG)], etag='"a"')
    find(gh, cache)
    gh.routes[LIST_URL] = LIMITED
    good = gh.routes[dl_url("fw-v1.2.0", "manifest.json")]
    gh.routes[dl_url("fw-v1.2.0", "manifest.json")] = good.replace(b"Faster", b"Slower")   # tampered
    with pytest.raises(E.BadSignature):
        find(gh, cache)
    gh.routes[dl_url("fw-v1.2.0", "manifest.json")] = good
    with pytest.raises(E.NoTrustKey):
        G.find_release(gh, None, plugin_version="1.0.0", installed_fw="1.0.0", cache=cache)
    with pytest.raises(E.BadSignature):   # another key's signature
        raw, sig = sign_doc(manifest_doc(IMG, "1.2.0"), OTHER_SEED)
        gh.routes[dl_url("fw-v1.2.0", "manifest.json")], gh.routes[dl_url("fw-v1.2.0", "manifest.sig")] = raw, sig
        find(gh, cache)


# --- rate limited, no cache: github.com download URLs ------------------------------------------
def latest_routes(gh, version="1.2.0", seed=None):
    gh.publish(version, IMG, **({"seed": seed} if seed else {}))
    for n in ("manifest.json", "manifest.sig"):
        gh.routes[LATEST + n] = gh.routes[dl_url(f"fw-v{version}", n)]
    gh.routes[LIST_URL] = LIMITED


def test_no_cache_stable_plugin_falls_back_to_latest_download(gh, cache):
    latest_routes(gh)
    r = find(gh, cache)
    assert r.tag == "fw-v1.2.0" and r.manifest.version == "1.2.0"
    assert r.bin_url == dl_url("fw-v1.2.0", "inkterface-ee05-1.2.0.bin")
    assert r.page_url == f"https://github.com/{G.REPO}/releases/tag/fw-v1.2.0"
    assert cache.limited is not None
    assert G.download_image(gh, r) == IMG   # the install path is unchanged


def test_no_cache_without_a_cache_object_also_falls_back(gh):
    latest_routes(gh)
    assert find(gh).tag == "fw-v1.2.0"


def test_latest_download_is_signature_gated(gh, cache):
    latest_routes(gh, seed=OTHER_SEED)
    with pytest.raises(E.BadSignature):
        find(gh, cache)
    latest_routes(gh)
    gh.routes[LATEST + "manifest.json"] = gh.routes[LATEST + "manifest.json"].replace(b"Faster", b"Slower")
    with pytest.raises(E.BadSignature):
        find(gh, cache)
    latest_routes(gh)
    with pytest.raises(E.NoTrustKey):
        G.find_release(gh, None, plugin_version="1.0.0", installed_fw="1.0.0")


def test_latest_download_keeps_the_policy_checks(gh):
    latest_routes(gh)
    assert G.find_release(gh, PUB, plugin_version="1.0.0", installed_fw="1.2.0") is None      # up to date
    with pytest.raises(E.Downgrade):
        G.find_release(gh, PUB, plugin_version="1.0.0", installed_fw="2.0.0")
    latest_routes(gh, "1.4.0")
    gh.routes[LATEST + "manifest.json"], gh.routes[LATEST + "manifest.sig"] = sign_doc(
        manifest_doc(IMG, "1.4.0", min_plugin_version="9.0.0"))
    with pytest.raises(E.PluginTooOld):
        find(gh)


def test_prerelease_plugin_does_not_use_latest_download(gh):
    latest_routes(gh)
    with pytest.raises(E.RateLimited):
        find(gh, include_prerelease=True)
    assert all(not u.startswith(LATEST) for u, _ in gh.calls)


def test_latest_download_missing_reports_the_rate_limit(gh):
    gh.routes[LIST_URL] = LIMITED          # e.g. the newest release is a plugin release: no manifest there -> 404
    with pytest.raises(E.RateLimited) as e:
        find(gh)
    assert e.value.retry_at == 1893456000.0
    gh.routes[LATEST + "manifest.json"] = E.NetworkError("offline")
    with pytest.raises(E.RateLimited):
        find(gh)


# --- headers ----------------------------------------------------------------------------------
def test_retry_after_beats_reset_and_both_are_carried(gh):
    gh.routes[LIST_URL] = G.Response(403, headers={"retry-after": "90", "x-ratelimit-reset": "1893456000"})
    with pytest.raises(E.RateLimited) as e:
        find(gh)
    assert (e.value.retry_after, e.value.retry_at) == (90.0, 1893456000.0)
    gh.routes[LIST_URL] = G.Response(403, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "soon"})
    with pytest.raises(E.RateLimited) as e:
        find(gh)
    assert (e.value.retry_after, e.value.retry_at) == (None, None)


def test_plain_403_is_not_a_rate_limit(gh):
    gh.routes[LIST_URL] = G.Response(403, headers={"x-ratelimit-remaining": "12"})
    with pytest.raises(E.BadResponse):
        find(gh)
