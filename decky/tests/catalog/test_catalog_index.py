"""Index schema + signature verification (verify_and_parse / parse_index)."""
import copy

import pytest
from catalog_testlib import (OTHER_SEED, PUB, canonical_bytes, entry_dict, index_doc, make_screen, sha,
                             sign_index)

from inkterface_core.catalog import index as IDX
from inkterface_core.catalog.index import CatalogError, NoCatalogKey, parse_index, verify_and_parse


def raw(doc):
    return canonical_bytes(doc)


def ok_doc(**over):
    d = index_doc([entry_dict()])
    d.update(over)
    return d


# --- happy path -----------------------------------------------------------------
def test_valid_index_parses_and_verifies():
    doc = index_doc([entry_dict("alpha"), entry_dict("beta", "2.1.0")])
    r, sig = sign_index(doc)
    idx = verify_and_parse(r, sig, PUB)
    assert idx.schema == 1 and idx.serial == 1000 and len(idx.entries) == 2
    assert [e.id for e in idx.entries] == ["alpha", "beta"]
    e = idx.entries[1]
    assert (e.version, e.zip, e.preview) == ("2.1.0", "zips/beta-2.1.0.zip", "previews/beta-2.1.0.png")


# --- schema ---------------------------------------------------------------------
def test_unknown_top_level_key_rejected():
    with pytest.raises(CatalogError, match="unknown top-level key"):
        parse_index(raw(ok_doc(extra=1)))


def test_unknown_entry_key_rejected():
    e = entry_dict()
    e["surprise"] = 1
    with pytest.raises(CatalogError, match="unknown key"):
        parse_index(raw(index_doc([e])))


def test_missing_entry_key_rejected():
    e = entry_dict()
    del e["sha256"]
    with pytest.raises(CatalogError, match="missing key"):
        parse_index(raw(index_doc([e])))


def test_bad_zip_path_rejected():
    e = entry_dict("demo", "1.0.0")
    e["zip"] = "elsewhere/demo-1.0.0.zip"
    with pytest.raises(CatalogError, match="zip must be"):
        parse_index(raw(index_doc([e])))


def test_path_must_match_own_id_and_version():
    e = entry_dict("demo", "1.0.0")
    e["preview"] = "previews/demo-2.0.0.png"   # version in path != entry version
    with pytest.raises(CatalogError, match="preview must be"):
        parse_index(raw(index_doc([e])))
    e2 = entry_dict("demo", "1.0.0")
    e2["zip"] = "zips/other-1.0.0.zip"
    with pytest.raises(CatalogError, match="zip must be"):
        parse_index(raw(index_doc([e2])))


def test_duplicate_id_rejected():
    with pytest.raises(CatalogError, match="duplicate screen id"):
        parse_index(raw(index_doc([entry_dict("dup", "1.0.0"), entry_dict("dup", "1.1.0")])))


def test_unsorted_index_is_refused():
    # Decision: the publisher sorts by id; an out-of-order index is a malformed build and is refused.
    doc = {"schema": 1, "serial": 1, "generated": "2026-11-11T00:00:00Z",
           "screens": [entry_dict("zeta"), entry_dict("alpha")]}   # NOT sorted
    with pytest.raises(CatalogError, match="sorted by id"):
        parse_index(raw(doc))


def test_sorted_index_is_accepted():
    doc = {"schema": 1, "serial": 1, "generated": "2026-11-11T00:00:00Z",
           "screens": [entry_dict("alpha"), entry_dict("zeta")]}
    assert [e.id for e in parse_index(raw(doc)).entries] == ["alpha", "zeta"]


def test_too_big_index_rejected():
    big = b"x" * (IDX.MAX_INDEX_BYTES + 1)
    with pytest.raises(CatalogError, match="larger than"):
        parse_index(big)


def test_too_many_screens_rejected():
    doc = index_doc([entry_dict(f"s{i:04d}") for i in range(IDX.MAX_SCREENS + 1)])
    with pytest.raises(CatalogError, match="max"):
        parse_index(raw(doc))


@pytest.mark.parametrize("field,value", [
    ("schema", 2), ("serial", "x"), ("serial", -1), ("serial", True),
])
def test_bad_top_types(field, value):
    with pytest.raises(CatalogError):
        parse_index(raw(ok_doc(**{field: value})))


@pytest.mark.parametrize("field,value", [
    ("has_code", "yes"), ("size", "10"), ("size", True), ("size", 0),
    ("size", IDX.MAX_ZIP_SIZE + 1), ("sha256", "nothex"), ("version", "1.2"),
    ("min_plugin_version", "bad"), ("name", 5), ("id", "Bad_Id"),
])
def test_bad_entry_types(field, value):
    e = entry_dict("demo", "1.0.0")
    e[field] = value
    # id/path fields are cross-checked against the path, so also fix the path for id/version changes when needed
    with pytest.raises(CatalogError):
        parse_index(raw(index_doc([e])))


def test_not_an_object():
    with pytest.raises(CatalogError, match="JSON object"):
        parse_index(b"[]")


def test_invalid_json():
    with pytest.raises(CatalogError, match="not valid JSON"):
        parse_index(b"{not json")


def test_duplicate_json_key():
    with pytest.raises(CatalogError, match="duplicate key"):
        parse_index(b'{"schema": 1, "schema": 1, "serial": 1, "generated": "x", "screens": []}')


# --- signature ------------------------------------------------------------------
def test_good_signature():
    doc = ok_doc()
    r, sig = sign_index(doc)
    assert verify_and_parse(r, sig, PUB).serial == 1000


def test_empty_key_fails_closed():
    r, sig = sign_index(ok_doc())
    with pytest.raises(NoCatalogKey):
        verify_and_parse(r, sig, None)
    with pytest.raises(NoCatalogKey):
        verify_and_parse(r, sig, b"")


def test_tampered_index_refused():
    r, sig = sign_index(ok_doc())
    tampered = r.replace(b'"serial": 1000', b'"serial": 9999')
    assert tampered != r
    with pytest.raises(CatalogError, match="signature does not match"):
        verify_and_parse(tampered, sig, PUB)


def test_bad_sig_length_refused():
    r, sig = sign_index(ok_doc())
    with pytest.raises(CatalogError, match="64 raw bytes"):
        verify_and_parse(r, sig[:-1], PUB)


def test_wrong_key_refused():
    r, sig = sign_index(ok_doc(), seed=OTHER_SEED)   # signed with another key
    with pytest.raises(CatalogError, match="signature does not match"):
        verify_and_parse(r, sig, PUB)


def test_signature_checked_before_parse():
    # a tampered body that is ALSO schema-invalid must fail on the SIGNATURE, not the schema
    r, sig = sign_index(ok_doc())
    broken = r.replace(b'"schema": 1', b'"schema": 7')
    with pytest.raises(CatalogError, match="signature does not match"):
        verify_and_parse(broken, sig, PUB)
