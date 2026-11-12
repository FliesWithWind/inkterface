"""Build firmware/host_test/idle_render_host and render a screen package's idle section with its
sample-data.json into a PNG (what the panel would show). Used by the golden tests and by
`python3 decky/tests/idle/idlerender.py <screen dir> <out.png>`."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
FW = os.path.join(ROOT, "firmware")
sys.path.insert(0, os.path.join(ROOT, "decky", "py_modules"))

from inkterface_core.idle.compile import compile_layout  # noqa: E402
from inkterface_core.idle.preview import sample_lines  # noqa: E402,F401  (the plugin and the tests share one implementation)

SRCS = ["idle_render", "idle_canvas", "idle_layout", "idle_fonts_data"]
_exe_cache: dict[str, str] = {}


def have_gpp() -> bool:
    return shutil.which("g++") is not None


def build_harness(workdir: str) -> str:
    if workdir in _exe_cache:
        return _exe_cache[workdir]
    exe = os.path.join(workdir, "idle_render_host")
    cmd = ["g++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror", f"-I{FW}/src/ee05",
           f"{FW}/host_test/idle_render_host.cpp", f"{FW}/src/ee05/usbproto.cpp"]
    cmd += [f"{FW}/src/ee05/{s}.cpp" for s in SRCS] + ["-o", exe]
    subprocess.run(cmd, check=True)
    _exe_cache[workdir] = exe
    return exe


def render_idle(idle: dict, sample: dict, out_png: str, workdir: str, assets_dir: str | None = None) -> None:
    """Same as render_screen for an in-memory `idle` section (widget-level tests)."""
    exe = build_harness(workdir)
    binp = os.path.join(workdir, "layout.bin")
    txt = os.path.join(workdir, "sample.txt")
    with open(binp, "wb") as f:
        f.write(compile_layout(idle, assets_dir=assets_dir))
    with open(txt, "w", encoding="utf-8") as f:
        f.write(sample_lines(idle, sample))
    subprocess.run([exe, binp, txt, out_png], check=True)


def render_screen(screen_dir: str, out_png: str, workdir: str, sample_override: dict | None = None) -> None:
    screen = json.load(open(os.path.join(screen_dir, "screen.json"), encoding="utf-8"))
    sample = sample_override if sample_override is not None else json.load(
        open(os.path.join(screen_dir, "sample-data.json"), encoding="utf-8"))
    exe = build_harness(workdir)
    binp = os.path.join(workdir, "layout.bin")
    txt = os.path.join(workdir, "sample.txt")
    with open(binp, "wb") as f:
        f.write(compile_layout(screen["idle"], assets_dir=os.path.join(screen_dir, "assets")))
    with open(txt, "w", encoding="utf-8") as f:
        f.write(sample_lines(screen["idle"], sample))
    subprocess.run([exe, binp, txt, out_png], check=True)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as d:
        render_screen(sys.argv[1], sys.argv[2], d)
        print("wrote", sys.argv[2])
