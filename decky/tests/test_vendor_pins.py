"""Pillow is not in git: scripts/vendor.sh fetches the sha256-pinned wheel named in vendor-requirements.txt.

Offline checks always run. The two that really download (7 MB from PyPI) run with INKTERFACE_NETWORK=1 (CI sets it).
"""
import os
import re
import subprocess
from pathlib import Path

import pytest

DECKY = Path(__file__).resolve().parents[1]
REQ = DECKY / "vendor-requirements.txt"
SCRIPT = DECKY / "scripts" / "vendor.sh"
NET = pytest.mark.skipif(not os.environ.get("INKTERFACE_NETWORK"), reason="set INKTERFACE_NETWORK=1 (downloads from PyPI)")


def pins(name="pillow"):
    text = REQ.read_text().replace("\\\n", " ")
    m = re.search(rf"^{name}==([\d.]+)\s+((?:--hash=sha256:[0-9a-f]{{64}}\s*)+)$", text, re.M)
    assert m, f"vendor-requirements.txt must pin '{name}==X.Y.Z --hash=sha256:<64 hex>'"
    return m.group(1), set(re.findall(r"[0-9a-f]{64}", m.group(2)))


def vendor(dest, req=REQ, **kw):
    env = {**os.environ, "VENDOR_DEST": str(dest), "VENDOR_REQUIREMENTS": str(req)}
    return subprocess.run([str(SCRIPT)], capture_output=True, text=True, env=env, **kw)


@pytest.mark.parametrize("name", ["pillow", "certifi"])
def test_the_requirement_is_pinned_to_a_version_and_a_hash(name):
    version, hashes = pins(name)
    assert version and hashes


def test_the_vendored_tree_is_the_pinned_wheel():
    """Run scripts/vendor.sh if this fails: py_modules/ holds something other than what the requirements pin."""
    import hashlib

    version, hashes = pins()
    cversion, chashes = pins("certifi")
    stamp = (DECKY / "py_modules" / ".vendored").read_text().splitlines()
    assert stamp[0] == hashlib.sha256(REQ.read_bytes()).hexdigest(), "requirements changed since the last vendor.sh run"
    assert {l.split()[0] for l in stamp[1:]} == {next(iter(hashes)), next(iter(chashes))}, "an unpacked wheel is not the pinned one"
    assert (DECKY / "py_modules" / f"pillow-{version}.dist-info").is_dir()
    assert (DECKY / "py_modules" / f"certifi-{cversion}.dist-info").is_dir()
    assert (DECKY / "py_modules" / "certifi" / "cacert.pem").stat().st_size > 100_000
    import PIL
    assert PIL.__version__ == version and PIL.__file__.startswith(str(DECKY / "py_modules"))


def test_vendor_is_a_noop_when_the_stamp_matches(tmp_path):
    import hashlib

    (tmp_path / "PIL").mkdir()
    (tmp_path / "PIL" / "Image.py").write_text("x")
    (tmp_path / "certifi").mkdir()
    (tmp_path / "certifi" / "cacert.pem").write_text("x")
    (tmp_path / ".vendored").write_text(hashlib.sha256(REQ.read_bytes()).hexdigest() + "\n")
    r = vendor(tmp_path)   # no network needed: nothing to fetch
    assert r.returncode == 0 and "up to date" in r.stdout and (tmp_path / "PIL").is_dir()


@NET
def test_vendor_installs_exactly_the_pinned_wheel(tmp_path):
    version, hashes = pins()
    r = vendor(tmp_path, timeout=300)
    assert r.returncode == 0, r.stderr
    _, *wheels = (tmp_path / ".vendored").read_text().splitlines()
    assert {w.split()[0] for w in wheels} == {next(iter(hashes)), next(iter(pins("certifi")[1]))}
    assert (tmp_path / "certifi" / "cacert.pem").is_file()
    assert (tmp_path / "PIL" / "Image.py").is_file() and (tmp_path / f"pillow-{version}.dist-info").is_dir()
    assert list(tmp_path.glob("PIL/_imaging.cpython-314-*.so"))
    # a stamp with half the tree gone (a checkout that deleted tracked files, an interrupted copy) is not "up to date"
    (tmp_path / "PIL" / "Image.py").unlink()
    r = vendor(tmp_path, timeout=300)
    assert r.returncode == 0 and "up to date" not in r.stdout and (tmp_path / "PIL" / "Image.py").is_file(), r.stderr


@NET
def test_vendor_refuses_a_wheel_that_does_not_match_the_pin(tmp_path):
    bad = tmp_path / "req.txt"
    version, _ = pins()
    bad.write_text(f"pillow=={version} --hash=sha256:{'0' * 64}\n")
    dest = tmp_path / "out"
    r = vendor(dest, req=bad, timeout=300)
    assert r.returncode != 0 and "DO NOT MATCH THE HASHES" in r.stderr
    assert not (dest / "PIL").exists() and not (dest / ".vendored").exists()
