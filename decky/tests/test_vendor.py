"""Imports inkterface_core with ONLY decky/py_modules on sys.path (isolated subprocess).

Run (needs CPython 3.14 on x86_64 Linux):
    uv run --python 3.14 --no-project --with pytest pytest decky/tests -q
"""
import subprocess
import sys
from pathlib import Path

# Stdlib modules present in the PyInstaller bundle of Decky Loader v3.3.1 (x86_64 PluginLoader binary).
# Regenerate: see docs/decky-runtime.md. PyInstaller drops stdlib modules Decky itself never imports
# (e.g. termios, which pyserial needs; hence inkterface_core/serialport.py). Builtins of a shared-lib 3.14 build are included.
BUNDLED = Path(__file__).with_name("decky_3.3.1_stdlib.txt")
PY_MODULES = Path(__file__).resolve().parents[1] / "py_modules"
OUT = Path(__file__).parent / "test_frame.png"

SNIPPET = """
import sys
sys.path[:] = [{pm!r}] + [p for p in sys.path if 'site-packages' not in p and 'dist-packages' not in p]
before = set(sys.modules)
import PIL, inkterface_core
from inkterface_core import diff, emulator, proto, usb_link  # the whole core must load in Decky's bundle
assert PIL.__file__.startswith({pm!r}), PIL.__file__
f = inkterface_core.render_test_frame()
assert isinstance(f, bytes) and len(f) == 38880, len(f)
assert any(b != 0xFF for b in f) and any(b == 0xFF for b in f)
assert isinstance(inkterface_core.list_serial_ports(), list)
import os
m, sl = os.openpty()
with inkterface_core.SerialPort.open(os.ttyname(sl)) as p:
    os.write(m, b"\\x00\\r\\n\\x11\\x13")
    assert p.read(16, timeout=1) == b"\\x00\\r\\n\\x11\\x13"
assert 'termios' not in sys.modules, 'termios must not be needed'
from PIL import Image
Image.frombytes('1', (648, 480), f).save({out!r})
new = {{m for m in set(sys.modules) - before if m.split('.')[0] in sys.stdlib_module_names}}
missing = sorted(m for m in new - set(open({bundled!r}).read().split()))
assert not missing, 'stdlib modules not in Decky bundle: %s' % missing
print(sys.version.split()[0], PIL.__version__, len(f))
"""


def test_vendored_imports_and_frame():
    code = SNIPPET.format(pm=str(PY_MODULES), out=str(OUT), bundled=str(BUNDLED))
    r = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    print(r.stdout)
    assert OUT.stat().st_size > 0


def _run(code, path):
    return subprocess.run([sys.executable, "-I", "-c", code.format(pm=str(path))], capture_output=True, text=True)


def test_stdlib_guard_catches_module_missing_from_bundle(tmp_path):
    """The bundle audit must fail when the allowlist lacks a module the plugin really imports (guards the guard)."""
    short = tmp_path / "short.txt"
    short.write_text("\n".join(m for m in BUNDLED.read_text().split() if m != "logging"))
    code = SNIPPET.format(pm=str(PY_MODULES), out=str(OUT), bundled=str(short))
    r = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True)
    assert r.returncode != 0 and "logging" in r.stderr


def test_wrong_abi_fails_loudly(tmp_path):
    """Pillow tree without the cp314 _imaging .so (= wheel for another ABI) raises ImportError, not a silent fallback."""
    pil = tmp_path / "PIL"
    pil.mkdir()
    for f in (PY_MODULES / "PIL").iterdir():
        if not f.name.startswith("_imaging.cpython-314"):
            (pil / f.name).symlink_to(f)
    (tmp_path / "pillow.libs").symlink_to(PY_MODULES / "pillow.libs")
    r = _run("import sys; sys.path[:] = [{pm!r}] + sys.path[1:]\nimport PIL.Image", tmp_path)
    assert r.returncode != 0 and "_imaging" in r.stderr


import os  # noqa: E402

import pytest  # noqa: E402


@pytest.mark.skipif(not os.environ.get("INKTERFACE_FROZEN"), reason="set INKTERFACE_FROZEN=1 (needs system python3.14 + network)")
def test_frozen_pyinstaller_stub():
    r = subprocess.run([str(Path(__file__).with_name("frozen_check.sh"))], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-800:]
    assert "FROZEN" in r.stdout and "38880" in r.stdout
