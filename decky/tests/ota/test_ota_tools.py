"""tools/release/*: key generation, manifest signing, and the whole chain
sign -> publish (fake GitHub) -> find -> download -> stream -> confirm."""
import importlib.util
import json
import os
import re
import stat

import pytest
from ota_helpers import PUB, SEED, Bench, FakeGitHub, make_image

from inkterface_core import proto as P
from inkterface_core.ota import ed25519, github, stream
from inkterface_core.ota import errors as E
from inkterface_core.ota import manifest as M

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "tools", "release", f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gen_keypair, sign_manifest = load("gen_keypair"), load("sign_manifest")
IMG = make_image(50_000)


def test_proto_version_matches_firmware_header():
    h = open(os.path.join(ROOT, "firmware", "src", "ee05", "usbproto.h")).read()
    assert int(re.search(r"PROTO_VER = (\d+)", h).group(1)) == P.PROTO_VER


def test_gen_keypair(tmp_path, capsys):
    priv, pub = tmp_path / "k", tmp_path / "k.pub"
    assert gen_keypair.main(["--private", str(priv), "--public", str(pub)]) == 0
    assert stat.S_IMODE(os.stat(priv).st_mode) == 0o600
    seed = bytes.fromhex(priv.read_text().strip())
    assert len(seed) == 32 and ed25519.publickey(seed).hex() == pub.read_text().strip()
    assert pub.read_text().strip() in capsys.readouterr().out
    # never overwrites: neither the private nor the public file
    assert gen_keypair.main(["--private", str(priv), "--public", str(tmp_path / "other.pub")]) == 1
    assert not (tmp_path / "other.pub").exists()
    before = priv.read_text()
    new_priv = tmp_path / "k2"
    assert gen_keypair.main(["--private", str(new_priv), "--public", str(pub)]) == 1
    assert not new_priv.exists()  # no orphan private key left behind
    assert priv.read_text() == before
    # two runs give different keys
    gen_keypair.main(["--private", str(tmp_path / "k3"), "--public", str(tmp_path / "k3.pub")])
    assert (tmp_path / "k3").read_text() != before


def test_test_fixture_key_is_consistent_and_not_production():
    assert open(os.path.join(FIX, "TEST-ONLY-signing.pub")).read().strip() == PUB.hex()
    from inkterface_core.ota import trust

    assert trust.PRODUCTION_PUBLIC_KEY_HEX != PUB.hex()
    assert trust.PRODUCTION_PUBLIC_KEY_HEX == "" or len(trust.PRODUCTION_PUBLIC_KEY_HEX) == 64
    if not trust.PRODUCTION_PUBLIC_KEY_HEX:
        assert trust.public_key() is None
    else:
        assert trust.public_key() == bytes.fromhex(trust.PRODUCTION_PUBLIC_KEY_HEX)


def make_bin(tmp_path, data=IMG):
    p = tmp_path / "firmware.bin"
    p.write_bytes(data)
    return str(p)


def sign(tmp_path, version="fw-v1.2.0", **kw):
    out = tmp_path / "dist"
    args = [make_bin(tmp_path), "--version", version, "--key-file", os.path.join(FIX, "TEST-ONLY-signing.key"), "--out-dir", str(out)]
    for k, v in kw.items():
        args += [f"--{k.replace('_', '-')}", str(v)]
    return out, sign_manifest.main(args)


def test_sign_manifest_outputs_verify(tmp_path, capsys):
    out, rc = sign(tmp_path, changelog="Fast refresh", min_plugin_version="1.1.0")
    assert rc == 0
    assert sorted(os.listdir(out)) == ["inkterface-ee05-1.2.0.bin", "manifest.json", "manifest.sig"]
    raw, sig = (out / "manifest.json").read_bytes(), (out / "manifest.sig").read_bytes()
    assert len(sig) == 64 and (out / "inkterface-ee05-1.2.0.bin").read_bytes() == IMG
    m = M.verify_and_parse(raw, sig, PUB)
    assert (m.version, m.size, m.protocol_version, m.min_plugin_version, m.changelog) == ("1.2.0", len(IMG), P.PROTO_VER, "1.1.0", "Fast refresh")
    assert json.loads(capsys.readouterr().out)["sha256"] == m.sha256
    with pytest.raises(E.BadSignature):  # another key must not verify it
        M.verify_and_parse(raw, sig, ed25519.publickey(b"\x02" * 32))


def test_sign_manifest_is_deterministic(tmp_path):
    out1, _ = sign(tmp_path)
    a = (out1 / "manifest.sig").read_bytes()
    out2, _ = sign(tmp_path)
    assert (out2 / "manifest.sig").read_bytes() == a


def test_sign_manifest_key_from_env_and_changelog_file(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_KEY_VAR", SEED.hex())
    cl = tmp_path / "cl.txt"
    cl.write_text("- one\n- two\n")
    out = tmp_path / "o"
    rc = sign_manifest.main([make_bin(tmp_path), "--version", "2.0.0", "--key-env", "TEST_KEY_VAR", "--out-dir", str(out), "--changelog-file", str(cl)])
    assert rc == 0
    m = M.verify_and_parse((out / "manifest.json").read_bytes(), (out / "manifest.sig").read_bytes(), PUB)
    assert m.changelog == "- one\n- two" and m.version == "2.0.0"


@pytest.mark.parametrize("bad", ["", "zz", "ab" * 31, "ab" * 33])
def test_sign_manifest_rejects_bad_key(tmp_path, monkeypatch, bad):
    monkeypatch.setenv("TEST_KEY_VAR", bad)
    with pytest.raises(SystemExit, match="64 hex"):
        sign_manifest.main([make_bin(tmp_path), "--version", "1.0.0", "--key-env", "TEST_KEY_VAR", "--out-dir", str(tmp_path / "o")])


def test_sign_manifest_refuses_non_image_and_bad_version_and_oversize(tmp_path):
    with pytest.raises(SystemExit, match="0xE9"):
        sign_manifest.main([make_bin(tmp_path, b"\x00" * 100), "--version", "1.0.0", "--key-file", os.path.join(FIX, "TEST-ONLY-signing.key"), "--out-dir", str(tmp_path / "o")])
    with pytest.raises(SystemExit, match="would reject"):
        sign(tmp_path, version="not-semver")
    with pytest.raises(SystemExit, match="would reject"):
        sign(tmp_path, min_plugin_version="x")
    big = make_bin(tmp_path, b"\xe9" + b"\0" * M.MAX_IMAGE)
    with pytest.raises(SystemExit, match="would reject"):
        sign_manifest.main([big, "--version", "1.0.0", "--key-file", os.path.join(FIX, "TEST-ONLY-signing.key"), "--out-dir", str(tmp_path / "o2")])
    assert not (tmp_path / "o2").exists()  # nothing published for a rejected manifest


def test_full_chain_release_to_flashed_panel(tmp_path):
    """sign_manifest output -> fake GitHub -> discovery -> download -> USB stream -> confirm."""
    out, _ = sign(tmp_path, version="fw-v1.2.0")
    gh = FakeGitHub()
    img_name = "inkterface-ee05-1.2.0.bin"
    for name in ("manifest.json", "manifest.sig", img_name):
        gh.routes[f"https://github.com/{github.REPO}/releases/download/fw-v1.2.0/{name}"] = (out / name).read_bytes()
    gh.listing([{
        "tag_name": "fw-v1.2.0", "draft": False, "prerelease": False, "html_url": "https://github.com/x",
        "assets": [{"name": n, "size": (out / n).stat().st_size,
                    "browser_download_url": f"https://github.com/{github.REPO}/releases/download/fw-v1.2.0/{n}"}
                   for n in ("manifest.json", "manifest.sig", img_name)],
    }])
    bench = Bench()  # panel runs 1.0.0
    installed = stream.handshake(bench.open_panel()[0], timeout=0.5).fw_ver
    rel = github.find_release(gh, PUB, plugin_version="1.0.0", installed_fw=installed)
    assert rel.manifest.version == "1.2.0"
    image = github.download_image(gh, rel)
    stream.install(bench.open_panel, image, rel.manifest.version, timeout=0.3, begin_timeout=1, end_timeout=1, ack_timeout=0.2, sleep=lambda s: None)
    assert bench.panel.fw_ver == "1.2.0" and bench.panel.installed == IMG and not bench.panel.pending_verify
    # and a second check now says: up to date
    assert github.find_release(gh, PUB, plugin_version="1.0.0", installed_fw="1.2.0") is None


# --- verify_release --------------------------------------------------------------------
verify_release = load("verify_release")


def test_verify_release_ok_and_rejections(tmp_path, capsys, monkeypatch):
    from inkterface_core.ota import trust

    monkeypatch.setattr(trust, "PRODUCTION_PUBLIC_KEY_HEX", "")
    out, _ = sign(tmp_path)
    assert verify_release.main([str(out), "--pubkey", PUB.hex()]) == 0
    assert "OK: inkterface-ee05-1.2.0.bin v1.2.0" in capsys.readouterr().out
    # no bundled production key: fail closed, even for a valid TEST signature
    assert verify_release.main([str(out)]) == 1
    assert "no release signing key" in capsys.readouterr().err
    # wrong key
    assert verify_release.main([str(out), "--pubkey", ed25519.publickey(b"\x03" * 32).hex()]) == 1
    # tampered image, truncated image, missing file
    img = out / "inkterface-ee05-1.2.0.bin"
    good = img.read_bytes()
    img.write_bytes(good[:-1] + bytes([good[-1] ^ 1]))
    assert verify_release.main([str(out), "--pubkey", PUB.hex()]) == 1
    img.write_bytes(good[:-5])
    assert verify_release.main([str(out), "--pubkey", PUB.hex()]) == 1
    img.unlink()
    assert verify_release.main([str(out), "--pubkey", PUB.hex()]) == 1
