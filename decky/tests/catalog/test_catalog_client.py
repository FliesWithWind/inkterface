"""Catalog client: refresh, atomic cache, serial-rollback, offline, zip/preview verification."""
import io

import pytest
from catalog_testlib import (BASE, OTHER_SEED, PUB, canonical_bytes, full_fetch, index_doc, make_screen,
                             preview_png, sha, sign_index)
from PIL import Image

from inkterface_core.catalog import Catalog
from inkterface_core.catalog.index import CatalogError, NoCatalogKey
from inkterface_core.packages.extract import PackageError


def client(tmp_path, fetch, key=PUB, url=BASE):
    return Catalog(tmp_path / "catalog", url, fetch=fetch, public_key=lambda: key)


# --- refresh --------------------------------------------------------------------
def test_refresh_fetches_verifies_and_caches(tmp_path):
    f, raw, sig, doc = full_fetch()
    c = client(tmp_path, f)
    idx, fetched_at = c.refresh()
    assert idx.serial == 1000 and len(idx.entries) == 1 and fetched_at > 0
    assert (c.dir / "index.json").read_bytes() == raw
    assert (c.dir / "index.json.sig").read_bytes() == sig
    assert BASE + "index.json" in f.calls and BASE + "index.json.sig" in f.calls


def test_refresh_empty_key_fails_closed(tmp_path):
    f, *_ = full_fetch()
    with pytest.raises(NoCatalogKey):
        client(tmp_path, f, key=None).refresh()


def test_refresh_network_down_keeps_cache(tmp_path):
    f, *_ = full_fetch()
    c = client(tmp_path, f)
    c.refresh()
    f.offline = True
    with pytest.raises(PackageError):
        c.refresh()
    # cache still usable
    idx, _ = c.cached()
    assert idx is not None and idx.serial == 1000


def test_refresh_bad_signature_does_not_replace_cache(tmp_path):
    f, raw, sig, doc = full_fetch(serial=1000)
    c = client(tmp_path, f)
    c.refresh()
    # now publish a tampered index at the same URLs
    bad = raw.replace(b'"serial": 1000', b'"serial": 2000')
    f.routes[BASE + "index.json"] = bad   # signature no longer matches
    with pytest.raises(CatalogError, match="signature"):
        c.refresh()
    idx, _ = c.cached()
    assert idx.serial == 1000   # unchanged


# --- serial rollback ------------------------------------------------------------
def test_rollback_refused_lower_serial(tmp_path):
    f, *_ = full_fetch(serial=1000)
    c = client(tmp_path, f)
    c.refresh()
    # a properly-signed OLDER index served at the same URLs
    f2, raw2, sig2, _ = full_fetch(serial=500)
    f.routes[BASE + "index.json"] = raw2
    f.routes[BASE + "index.json.sig"] = sig2
    with pytest.raises(CatalogError, match="older than the saved catalog"):
        c.refresh()
    assert c.cached()[0].serial == 1000


def test_equal_serial_is_accepted(tmp_path):
    f, *_ = full_fetch(serial=1000)
    c = client(tmp_path, f)
    c.refresh()
    f2, raw2, sig2, _ = full_fetch(serial=1000)
    f.routes[BASE + "index.json"] = raw2
    f.routes[BASE + "index.json.sig"] = sig2
    idx, _ = c.refresh()
    assert idx.serial == 1000


def test_higher_serial_replaces(tmp_path):
    f, *_ = full_fetch(serial=1000)
    c = client(tmp_path, f)
    c.refresh()
    f2, raw2, sig2, _ = full_fetch(serial=2000)
    f.routes[BASE + "index.json"] = raw2
    f.routes[BASE + "index.json.sig"] = sig2
    assert c.refresh()[0].serial == 2000


# --- offline cache --------------------------------------------------------------
def test_list_works_from_cache_when_refresh_fails(tmp_path):
    f, *_ = full_fetch()
    c = client(tmp_path, f)
    c.refresh()
    f.offline = True
    idx, fetched_at = c.cached()   # no network
    assert idx is not None and fetched_at is not None


def test_no_cache_yet_is_none(tmp_path):
    f, *_ = full_fetch()
    c = client(tmp_path, f)
    assert c.cached() == (None, None)


def test_cache_tampered_on_disk_refuses(tmp_path):
    f, *_ = full_fetch()
    c = client(tmp_path, f)
    c.refresh()
    p = c.dir / "index.json"
    p.write_bytes(p.read_bytes().replace(b'"serial": 1000', b'"serial": 7777'))
    with pytest.raises(CatalogError, match="signature"):
        c.cached()


def test_cache_survives_a_new_client(tmp_path):
    f, *_ = full_fetch()
    client(tmp_path, f).refresh()
    # a fresh client over the same dir (simulates a plugin restart), network offline
    f.offline = True
    c2 = client(tmp_path, f)
    idx, fetched_at = c2.cached()
    assert idx is not None and idx.serial == 1000


# --- zip download verification --------------------------------------------------
def test_fetch_zip_ok(tmp_path):
    screen = make_screen("demo", "1.0.0")
    f, *_ = full_fetch(screens=[screen])
    c = client(tmp_path, f)
    idx, _ = c.refresh()
    data = c.fetch_zip(idx.entries[0])
    assert data == screen[1]


def test_fetch_zip_sha_mismatch(tmp_path):
    entry, zbytes, prev = make_screen("demo", "1.0.0")
    f, *_ = full_fetch(screens=[(entry, zbytes, prev)])
    c = client(tmp_path, f)
    idx, _ = c.refresh()
    tampered = bytearray(zbytes)
    tampered[-1] ^= 0xFF   # same length, different bytes -> size passes, sha fails
    f.routes[BASE + entry["zip"]] = bytes(tampered)
    with pytest.raises(CatalogError, match="sha256 does not match"):
        c.fetch_zip(idx.entries[0])


def test_fetch_zip_size_mismatch(tmp_path):
    entry, zbytes, prev = make_screen("demo", "1.0.0")
    f, *_ = full_fetch(screens=[(entry, zbytes, prev)])
    c = client(tmp_path, f)
    idx, _ = c.refresh()
    # a different-length body that would otherwise be caught by size first
    f.routes[BASE + entry["zip"]] = zbytes[:-1]
    with pytest.raises(CatalogError, match="bytes, the catalog says"):
        c.fetch_zip(idx.entries[0])


# --- preview --------------------------------------------------------------------
def test_preview_downloads_verifies_and_caches(tmp_path):
    entry, zbytes, prev = make_screen("demo", "1.0.0")
    f, *_ = full_fetch(screens=[(entry, zbytes, prev)])
    c = client(tmp_path, f)
    idx, _ = c.refresh()
    png = c.preview_png(idx.entries[0])
    assert png == prev
    cached = c.dir / "previews" / "demo-1.0.0.png"
    assert cached.read_bytes() == prev
    # second call is served from disk (no new network fetch for the preview URL)
    before = f.calls.count(BASE + entry["preview"])
    assert c.preview_png(idx.entries[0]) == prev
    assert f.calls.count(BASE + entry["preview"]) == before


def test_preview_works_offline_from_cache(tmp_path):
    entry, zbytes, prev = make_screen("demo", "1.0.0")
    f, *_ = full_fetch(screens=[(entry, zbytes, prev)])
    c = client(tmp_path, f)
    idx, _ = c.refresh()
    c.preview_png(idx.entries[0])
    f.offline = True
    assert c.preview_png(idx.entries[0]) == prev


def test_preview_sha_mismatch_refused(tmp_path):
    entry, zbytes, prev = make_screen("demo", "1.0.0")
    f, *_ = full_fetch(screens=[(entry, zbytes, prev)])
    c = client(tmp_path, f)
    idx, _ = c.refresh()
    f.routes[BASE + entry["preview"]] = preview_png(color=200)   # different bytes
    with pytest.raises(CatalogError, match="preview sha256 does not match"):
        c.preview_png(idx.entries[0])


def test_preview_wrong_dimensions_refused(tmp_path):
    bad = preview_png(size=(320, 240))
    entry, zbytes, _ = make_screen("demo", "1.0.0", preview=bad)
    f, *_ = full_fetch(screens=[(entry, zbytes, bad)])
    c = client(tmp_path, f)
    idx, _ = c.refresh()
    with pytest.raises(CatalogError, match="648x480 PNG"):
        c.preview_png(idx.entries[0])


def test_preview_not_a_png_refused(tmp_path):
    junk = b"\x89not a png at all" * 4
    entry, zbytes, _ = make_screen("demo", "1.0.0", preview=junk)
    f, *_ = full_fetch(screens=[(entry, zbytes, junk)])
    c = client(tmp_path, f)
    idx, _ = c.refresh()
    with pytest.raises(CatalogError, match="not a readable PNG|648x480 PNG"):
        c.preview_png(idx.entries[0])


def test_preview_too_big_refused(tmp_path):
    from inkterface_core.catalog.index import MAX_PREVIEW_SIZE
    big = preview_png()
    entry, zbytes, _ = make_screen("demo", "1.0.0", preview=big)
    f, *_ = full_fetch(screens=[(entry, zbytes, big)])
    c = client(tmp_path, f)
    idx, _ = c.refresh()
    # serve an over-cap body at the preview URL
    f.routes[BASE + entry["preview"]] = b"x" * (MAX_PREVIEW_SIZE + 1)
    with pytest.raises(PackageError, match="exceeds"):
        c.preview_png(idx.entries[0])


# --- url normalisation ----------------------------------------------------------
def test_trailing_slash_added(tmp_path):
    f, *_ = full_fetch()
    c = Catalog(tmp_path / "catalog", "https://x.example/sub", fetch=f, public_key=lambda: PUB)
    assert c.url == "https://x.example/sub/"
