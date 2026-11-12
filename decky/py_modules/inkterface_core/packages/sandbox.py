"""Run an approved `screen.py` in a separate process. A GUARDRAIL against buggy screens, NOT a sandbox:
a malicious screen.py can still read your files and use the network as the plugin's user. Only approve code you trust.

What it does: separate interpreter (`-I`, so no PYTHON* env, no cwd on sys.path), empty environment, cwd = package folder,
wall-clock timeout (process group killed), `setrlimit` caps (memory, CPU, file size, no fork, few fds), response size cap,
strict validation of the returned image. Any failure raises PackageError; the store turns that into an error frame.
"""
from __future__ import annotations

import base64
import datetime
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
from collections.abc import Mapping
from pathlib import Path

from PIL import Image, ImageDraw

from ..screens.fonts import get_font
from ..screens.schema import HEIGHT, WIDTH
from .extract import PackageError

RUNNER = Path(__file__).with_name("_runner.py")
PY_MODULES = Path(__file__).resolve().parents[2]
TIMEOUT_S = 2.0
MEMORY_MB = 512
FSIZE_MB = 2
MAX_RESPONSE_BYTES = 1024 * 1024
STDERR_TAIL = 400


def find_python() -> str | None:
    """An interpreter for the child. Inside Decky `sys.executable` is the frozen loader, so look on PATH instead."""
    exe = None if getattr(sys, "frozen", False) else sys.executable
    return exe or shutil.which("python3")


def _check_png(png: bytes) -> Image.Image:
    try:
        im = Image.open(io.BytesIO(png))
        fmt, size, mode = im.format, im.size, im.mode
    except Exception:  # noqa: BLE001 - PIL raises a zoo of types on garbage
        raise PackageError("screen.py: output is not a PNG image") from None
    if fmt != "PNG":
        raise PackageError(f"screen.py: output is {fmt}, expected PNG")
    if size != (WIDTH, HEIGHT):
        raise PackageError(f"screen.py: image is {size[0]}x{size[1]}, expected {WIDTH}x{HEIGHT}")
    if mode != "1":
        raise PackageError(f"screen.py: image mode is '{mode}', expected 1-bit ('1')")
    try:
        im.load()
    except Exception:  # noqa: BLE001
        raise PackageError("screen.py: PNG data is corrupt") from None
    return im


def _describe_exit(rc: int) -> str:
    if rc < 0:
        try:
            return f"killed by {signal.Signals(-rc).name}"
        except ValueError:
            return f"killed by signal {-rc}"
    return f"exit code {rc}"


def _json_default(o):
    """The live data snapshot holds an aware datetime (`time.now`); the child sees it as an ISO string."""
    if isinstance(o, (datetime.datetime, datetime.date)):
        return o.isoformat()
    raise TypeError(f"{type(o).__name__} is not JSON serialisable")


def run_code(code: bytes, cwd: Path, data: Mapping, settings: Mapping, *, timeout: float = TIMEOUT_S,
             python: str | None = None, memory_mb: int = MEMORY_MB) -> Image.Image:
    """Run `code` (the exact bytes the caller verified) and return its 648x480 1-bit image. Raises PackageError."""
    py = python or find_python()
    if not py:
        raise PackageError("screen.py: no Python interpreter found to run it (install python3)")
    try:
        source = code.decode("utf-8")
        request = json.dumps({
            "code": source, "path": str(Path(cwd) / "screen.py"), "size": [WIDTH, HEIGHT],
            "data": data, "settings": settings, "sys_path": [str(PY_MODULES)],
            "limits": {"memory_mb": memory_mb, "cpu_s": int(timeout) + 1, "fsize_mb": FSIZE_MB},
        }, default=_json_default).encode()
    except UnicodeDecodeError:
        raise PackageError("screen.py: not valid UTF-8") from None
    except (TypeError, ValueError) as e:
        raise PackageError(f"data/settings are not JSON-serialisable: {e}") from None

    with tempfile.TemporaryDirectory(prefix="inkterface-run-") as td:
        env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "HOME": td, "TMPDIR": td, "PYTHONDONTWRITEBYTECODE": "1"}
        # stdout/stderr go to files, not pipes: RLIMIT_FSIZE bounds them, so a flood cannot fill our memory.
        with open(Path(td) / "out", "w+b") as out, open(Path(td) / "err", "w+b") as err:
            try:
                proc = subprocess.Popen([py, "-I", str(RUNNER)], stdin=subprocess.PIPE, stdout=out, stderr=err,
                                        cwd=cwd, env=env, start_new_session=True, close_fds=True)
            except OSError as e:
                raise PackageError(f"screen.py: cannot start {py}: {e.strerror or e}") from None
            try:
                proc.communicate(request, timeout=timeout)
            except subprocess.TimeoutExpired:
                raise PackageError(f"screen.py: timed out after {timeout:g}s") from None
            finally:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)   # also anything it left behind
                except (ProcessLookupError, PermissionError):
                    pass
                proc.wait()                               # reap
            out.seek(0)
            body = out.read(MAX_RESPONSE_BYTES + 1)
            err.seek(0, os.SEEK_END)
            err.seek(max(err.tell() - STDERR_TAIL, 0))
            tail = err.read().decode("utf-8", "replace").strip()

    if proc.returncode != 0:
        raise PackageError(f"screen.py: process {_describe_exit(proc.returncode)}" + (f": {tail}" if tail else ""))
    if len(body) > MAX_RESPONSE_BYTES:
        raise PackageError(f"screen.py: output larger than {MAX_RESPONSE_BYTES} bytes")
    try:
        resp = json.loads(body)
        if not resp["ok"]:
            raise PackageError(f"screen.py: {resp['error']}")
        png = base64.b64decode(resp["png"], validate=True)
    except PackageError:
        raise
    except (ValueError, KeyError, TypeError):
        raise PackageError("screen.py: output is not a valid response") from None
    return _check_png(png)


def error_frame(title: str, message: str) -> Image.Image:
    """What the panel shows instead of a screen that failed."""
    img = Image.new("1", (WIDTH, HEIGHT), 255)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, WIDTH - 1, HEIGHT - 1), outline=0, width=4)
    d.text((24, 24), "SCREEN ERROR", font=get_font("bold-32"), fill=0)
    d.text((24, 72), title[:50], font=get_font("regular-20"), fill=0)
    for n, line in enumerate(textwrap.wrap(message, 60, break_long_words=True)[:12]):
        d.text((24, 120 + n * 22), line, font=get_font("regular-16"), fill=0)
    return img
