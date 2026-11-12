"""Compile and run the C++ host tests of the idle firmware units (firmware/host_test/idle_*_host.cpp):
state machine + MODE/IDLE_LAYOUT framing, store parsing, two-slot flash with power loss at every
write, chunked uploader, layout set/rotation, painter cadence, renderer unit checks. Also: the
generated font and fallback-layout sources must match their generators."""
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
FW = os.path.join(ROOT, "firmware")
SRC = f"{FW}/src/ee05"

pytestmark = pytest.mark.skipif(shutil.which("g++") is None, reason="g++ not available")

COMMON = ["g++", "-std=c++17", "-O1", "-g", "-Wall", "-Wextra", "-Werror", f"-I{SRC}"]
IDLE = ["layout", "store", "layoutset", "painter", "render", "canvas", "fonts_data"]


def build_run(tmp_path, main, srcs, args=()):
    exe = str(tmp_path / main)
    subprocess.run(COMMON + [f"{FW}/host_test/{main}.cpp"] + [f"{SRC}/{s}.cpp" for s in srcs] + ["-o", exe], check=True)
    return subprocess.run([exe, *args], capture_output=True, text=True)


def test_idle_mode_host(tmp_path):
    r = build_run(tmp_path, "idle_mode_host", [f"idle_{s}" for s in IDLE] + ["usbproto"])
    assert r.returncode == 0, r.stdout + r.stderr
    for part in ("controller ok", "device ok", "parseStore ok", "flash store ok", "uploader ok",
                 "layoutset ok", "painter ok", "idle_mode host tests OK"):
        assert part in r.stdout


def test_idle_render_selftest(tmp_path):
    r = build_run(tmp_path, "idle_render_host", [f"idle_{s}" for s in ("render", "canvas", "layout", "fonts_data")] + ["usbproto"],
                  ["--selftest"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert "selftest OK" in r.stdout


def test_generated_sources_are_fresh():
    # The generators need Pillow. Hand them the vendored one, not whatever the machine happens to have installed
    # (the freshness check must not depend on a system Pillow: CI runners have none).
    env = {**os.environ, "PYTHONPATH": f"{ROOT}/decky/py_modules"}
    assert subprocess.run([sys.executable, f"{FW}/scripts/gen_idle_fonts.py", "--check"], env=env).returncode == 0, \
        "idle_fonts_data.cpp is stale: run firmware/scripts/gen_idle_fonts.py"
    assert subprocess.run([sys.executable, f"{ROOT}/decky/tools/gen_idle_fallback.py", "--check"], env=env).returncode == 0, \
        "idle_fallback.h is stale: run decky/tools/gen_idle_fallback.py"


ARDUINOJSON = f"{FW}/.pio/libdeps/ee05/ArduinoJson/src"


@pytest.mark.skipif(not os.path.isdir(ARDUINOJSON), reason="ArduinoJson not fetched (run `pio run -e ee05` once)")
def test_idle_cfg_host(tmp_path):
    exe = str(tmp_path / "idle_cfg_host")
    subprocess.run(COMMON + [f"-I{ARDUINOJSON}", f"{FW}/host_test/idle_cfg_host.cpp", f"{SRC}/idle_store.cpp",
                             f"{SRC}/idle_layout.cpp", f"{SRC}/usbproto.cpp", "-o", exe], check=True)
    r = subprocess.run([exe], capture_output=True, text=True)
    assert r.returncode == 0 and "idle_cfg host tests OK" in r.stdout, r.stdout + r.stderr
