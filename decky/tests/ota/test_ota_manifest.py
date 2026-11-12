import json

import pytest
from ota_helpers import OTHER_SEED, PUB, SEED, make_image, manifest_doc, sign_doc

from inkterface_core import proto as P
from inkterface_core.ota import errors as E
from inkterface_core.ota import manifest as M

IMG = make_image()


# --- semver --------------------------------------------------------------------
def test_semver_ordering():
    order = ["0.9.9", "1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-alpha.beta", "1.0.0-beta", "1.0.0-beta.2",
             "1.0.0-beta.11", "1.0.0-rc.1", "1.0.0", "1.0.1", "1.1.0", "2.0.0", "10.0.0"]
    keys = [M.semver(v) for v in order]
    assert keys == sorted(keys) and len(set(keys)) == len(keys)


def test_semver_build_metadata_ignored_and_ints_numeric():
    assert M.semver("1.0.0+abc") == M.semver("1.0.0+def")
    assert M.semver("1.2.10") > M.semver("1.2.9")


@pytest.mark.parametrize("bad", ["", "1", "1.2", "1.2.3.4", "01.2.3", "1.2.3-", "1.2.3-01", "v1.2.3", "1.2.x", " 1.2.3", None, 5])
def test_semver_rejects(bad):
    with pytest.raises(ValueError):
        M.semver(bad)


def test_installed_version():
    assert M.installed_version("1.2.0") == "1.2.0"
    assert M.installed_version("fw-v1.2.0") == "1.2.0"
    assert M.installed_version("1.2.0-rc.1") == "1.2.0-rc.1"
    for dev in ("gV1-BLE-DIRTY", "gFW-V1.2.0-3-GABC123", "", "emulator"):
        assert M.installed_version(dev) is None


@pytest.mark.parametrize("dev", ["0.0.0-dev+308c457", "0.0.0-dev+308c457.dirty", "0.0.0-dev+unknown", "0.0.0-dev", "fw-v0.0.0-dev+abc1234"])
def test_installed_version_new_dev_string_is_a_dev_build(dev):
    assert M.installed_version(dev) is None


@pytest.mark.parametrize("rel", ["0.0.0", "0.0.1", "0.0.0-devel", "0.0.0-rc.1", "1.0.0-dev", "0.1.0-dev+x"])
def test_installed_version_lookalikes_are_not_dev_builds(rel):
    assert M.installed_version(rel) == rel


@pytest.mark.parametrize("v,pre", [("1.0.0", False), ("1.0.0-beta.1", True), ("1.0.0-rc.1+build.5", True), ("1.0.0+build.5", False),
                                   ("0.0.0-dev+abc", True)])
def test_is_prerelease(v, pre):
    assert M.is_prerelease(v) is pre


@pytest.mark.parametrize("bad", ["unknown", "1.0", "", None])
def test_is_prerelease_rejects_what_is_not_semver(bad):
    with pytest.raises(ValueError):
        M.is_prerelease(bad)


def test_dev_build_is_offered_any_release_and_a_release_still_blocks_downgrades():
    m = M.Manifest(version="1.0.0", protocol_version=P.PROTO_VER, sha256="0" * 64, size=1, min_plugin_version="0.0.1")
    assert M.check_policy(m, plugin_version="1.0.0", installed=M.installed_version("0.0.0-dev+308c457")) is True
    old = M.Manifest(version="0.0.5", protocol_version=P.PROTO_VER, sha256="0" * 64, size=1, min_plugin_version="0.0.1")
    assert M.check_policy(old, plugin_version="1.0.0", installed=M.installed_version("0.0.0-dev+308c457")) is True   # dev: no compare
    with pytest.raises(E.Downgrade):
        M.check_policy(old, plugin_version="1.0.0", installed=M.installed_version("0.1.0"))


# --- parse -----------------------------------------------------------------------
def parse(doc):
    return M.parse_manifest(json.dumps(doc).encode())


def test_parse_ok_and_defaults():
    m = parse(manifest_doc(IMG))
    assert (m.version, m.protocol_version, m.size, m.min_plugin_version) == ("1.2.0", P.PROTO_VER, len(IMG), "1.0.0")
    assert m.asset == "inkterface-ee05-1.2.0.bin"
    d = manifest_doc(IMG)
    del d["changelog"]
    assert parse(d).changelog == ""
    assert parse({**manifest_doc(IMG), "extra": [1, 2]}).version == "1.2.0"  # unknown fields ignored


@pytest.mark.parametrize("key", ["version", "protocol_version", "sha256", "size", "min_plugin_version"])
def test_parse_missing_required(key):
    d = manifest_doc(IMG)
    del d[key]
    with pytest.raises(E.BadManifest):
        parse(d)


@pytest.mark.parametrize(
    "over",
    [
        {"version": "1.2"}, {"version": 120}, {"min_plugin_version": "latest"},
        {"protocol_version": "1"}, {"protocol_version": True}, {"protocol_version": -1}, {"protocol_version": 1.0},
        {"size": 0}, {"size": -5}, {"size": M.MAX_IMAGE + 1}, {"size": "100"}, {"size": False},
        {"sha256": "A" * 64}, {"sha256": "a" * 63}, {"sha256": "g" * 64}, {"sha256": 5},
        {"changelog": 5}, {"changelog": "x" * (M.MAX_CHANGELOG + 1)},
    ],
)
def test_parse_rejects_bad_fields(over):
    with pytest.raises(E.BadManifest):
        parse(manifest_doc(IMG, **over))


def test_parse_rejects_non_json_and_wrong_shape_and_dup_keys():
    for raw in (b"", b"not json", b"\xff\xfe", b"[1,2]", b'"str"', b"null"):
        with pytest.raises(E.BadManifest):
            M.parse_manifest(raw)
    good = json.dumps(manifest_doc(IMG))
    dup = good[:-1] + ', "size": 1}'
    with pytest.raises(E.BadManifest, match="duplicate"):
        M.parse_manifest(dup.encode())
    with pytest.raises(E.BadManifest, match="large"):
        M.parse_manifest(b" " * (M.MAX_MANIFEST + 1))


def test_size_boundaries_accepted():
    assert parse(manifest_doc(IMG, size=1)).size == 1
    assert parse(manifest_doc(IMG, size=M.MAX_IMAGE)).size == M.MAX_IMAGE


# --- signature -------------------------------------------------------------------
def test_verify_and_parse_ok():
    raw, sig = sign_doc(manifest_doc(IMG))
    assert M.verify_and_parse(raw, sig, PUB).version == "1.2.0"


def test_unsigned_and_wrong_length_signature():
    raw, _ = sign_doc(manifest_doc(IMG))
    for sig in (b"", b"\0" * 64, b"\0" * 63, b"\0" * 65):
        with pytest.raises(E.BadSignature):
            M.verify_and_parse(raw, sig, PUB)


def test_wrong_key():
    raw, sig = sign_doc(manifest_doc(IMG), OTHER_SEED)
    with pytest.raises(E.BadSignature):
        M.verify_and_parse(raw, sig, PUB)


def test_no_trust_key_fails_closed():
    raw, sig = sign_doc(manifest_doc(IMG))
    for key in (None, b""):
        with pytest.raises(E.NoTrustKey):
            M.verify_and_parse(raw, sig, key)


def test_one_bit_flip_in_manifest_sig_or_key_rejected():
    raw, sig = sign_doc(manifest_doc(IMG))

    def flip(b, i):
        x = bytearray(b)
        x[i // 8] ^= 1 << (i % 8)
        return bytes(x)

    for i in range(0, len(raw) * 8, 7):  # sampled across the whole manifest
        with pytest.raises((E.BadSignature, E.BadManifest)):
            M.verify_and_parse(flip(raw, i), sig, PUB)
    for i in range(0, 512, 5):
        with pytest.raises(E.BadSignature):
            M.verify_and_parse(raw, flip(sig, i), PUB)
    for i in range(0, 256, 5):
        with pytest.raises(E.BadSignature):
            M.verify_and_parse(raw, sig, flip(PUB, i))


def test_signature_checked_before_parsing():
    # properly signed garbage -> BadManifest; unsigned garbage -> BadSignature (never parsed)
    from inkterface_core.ota import ed25519

    junk = b"not json"
    with pytest.raises(E.BadManifest):
        M.verify_and_parse(junk, ed25519.sign(SEED, junk), PUB)
    with pytest.raises(E.BadSignature):
        M.verify_and_parse(junk, b"\0" * 64, PUB)


# --- policy ----------------------------------------------------------------------
def m(**over):
    return parse(manifest_doc(IMG, **over))


def policy(man, installed="1.0.0", plugin="1.0.0", **kw):
    return M.check_policy(man, plugin_version=plugin, installed=installed, **kw)


def test_policy_newer_is_offered():
    assert policy(m(version="1.2.0")) is True
    assert policy(m(version="1.0.1-rc.1"), installed="1.0.0") is True  # pre-release of a later patch
    assert policy(m(version="1.2.0"), installed=None) is True  # dev build: anything is newer


def test_policy_same_version_is_up_to_date_not_error():
    assert policy(m(version="1.0.0")) is False
    assert policy(m(version="1.0.0+build7"), installed="1.0.0") is False


def test_policy_downgrade_refused():
    with pytest.raises(E.Downgrade):
        policy(m(version="0.9.0"))
    with pytest.raises(E.Downgrade):
        policy(m(version="1.0.0-rc.1"), installed="1.0.0")  # pre-release is below the release


def test_policy_protocol_mismatch():
    with pytest.raises(E.ProtocolMismatch):
        policy(m(protocol_version=P.PROTO_VER + 1))
    with pytest.raises(E.ProtocolMismatch):
        policy(m(protocol_version=0))
    assert policy(m(protocol_version=2), proto_version=2) is True


def test_policy_min_plugin_version():
    with pytest.raises(E.PluginTooOld):
        policy(m(min_plugin_version="1.1.0"), plugin="1.0.9")
    assert policy(m(min_plugin_version="1.1.0"), plugin="1.1.0") is True
    assert policy(m(min_plugin_version="1.1.0"), plugin="2.0.0") is True
    with pytest.raises(E.PluginTooOld):  # pre-release plugin is below its release
        policy(m(min_plugin_version="1.1.0"), plugin="1.1.0-beta.1")
