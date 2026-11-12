"""Idle-screen preview: run the firmware's own idle renderer (firmware/host_test/idle_render_host.cpp, built static by
decky/scripts/build_idle_render.sh and shipped in the plugin zip as bin/idle_render_host) on a compiled layout and
return what the panel would draw as a 648x480 PNG.

No compiler on SteamOS, so the binary is prebuilt. Decky's zip extraction may drop the exec bit: `IdleRenderer`
copies the binary once into a private 0700 directory and runs that copy. The child gets an argv list (no shell),
an empty environment, a throwaway working directory and a timeout; its stderr is never passed on.
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from PIL import Image

from .compile import LayoutError, compile_layout

log = logging.getLogger(__name__)

SIZE = (648, 480)
TIMEOUT_S = 10.0
MAX_PNG = 2_000_000   # the harness writes ~40-80 KB (stored deflate); anything near this is not our renderer
_KEY = re.compile(r"^(\w+)\.([A-Za-z_.]+?)(?:\[(\d+)\])?(?:\.([A-Za-z_0-9.]+))?$")


class PreviewError(Exception):
    """A user-presentable reason the preview could not be made (short, no secrets, no renderer output)."""


def sample_lines(idle: dict, sample: dict) -> str:
    """sample-data.json -> harness sample.txt. keys are '<source>.<path>' with an optional [n].
    Raises ValueError for a key that names no source of this layout. Values that are neither number nor text are skipped."""
    names = list(idle.get("sources", {}))
    out = []
    if "epoch" in sample:
        out.append(f"epoch {int(sample['epoch'])}")
    if "tz" in sample:
        out.append("tz " + re.sub(r"[\r\n]", " ", str(sample["tz"])))
    for key, val in sample.get("values", {}).items():
        m = _KEY.match(key)
        if not m:
            raise ValueError(f"sample key '{key}' is not <source>.<path>")
        src, head, idx, tail = m.group(1), m.group(2), m.group(3), m.group(4)
        if src not in names:
            raise ValueError(f"sample key '{key}': no source '{src}' in this layout")
        path = head + ("." + tail if tail else "")
        if isinstance(val, bool) or not isinstance(val, (int, float, str)):
            continue
        kind = "N" if isinstance(val, (int, float)) else "S"
        out.append(f"{names.index(src)} {path} {idx or 0} {kind} " + re.sub(r"[\r\n]", " ", str(val)))
    return "\n".join(out) + "\n"


def load_sample(pkg_dir) -> dict:
    """The package's sample-data.json, or {} when absent or unusable (values then draw as placeholders)."""
    try:
        data = json.loads((Path(pkg_dir) / "sample-data.json").read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) and isinstance(data.get("values", {}), dict) else {}


class IdleRenderer:
    """Owns the private copy of the renderer binary. One per PluginCore; `render` may be called from any thread."""

    def __init__(self, private_dir) -> None:
        self._dir = Path(private_dir)
        self._lock = threading.Lock()
        self._copy: tuple[str, Path] | None = None   # (source path, private copy)

    def _exe(self, src: Path) -> Path:
        with self._lock:
            if self._copy and self._copy[0] == str(src) and self._copy[1].is_file():
                return self._copy[1]
            if not src.is_file():
                raise PreviewError("idle preview renderer is not installed")
            try:
                self._dir.mkdir(mode=0o700, parents=True, exist_ok=True)
                os.chmod(self._dir, 0o700)
                fd, tmp = tempfile.mkstemp(prefix=".new-", dir=self._dir)
                try:
                    with os.fdopen(fd, "wb") as out, open(src, "rb") as inp:
                        out.write(inp.read())
                    os.chmod(tmp, 0o700)
                    dest = self._dir / "idle_render_host"
                    os.replace(tmp, dest)   # atomic: never overwrites a binary that is running
                except BaseException:
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass
                    raise
            except OSError as e:
                log.warning("idle renderer copy failed: %s", e.strerror or type(e).__name__)
                raise PreviewError("idle preview renderer cannot be prepared") from None
            self._copy = (str(src), dest)
            return dest

    def render(self, src_bin, idle: dict, assets_dir, mode: str, sample: dict, *, now: float | None = None,
               tz: str = "UTC0") -> bytes:
        """PNG bytes (648x480; mode "1" for mono, "L" for gray4). Raises PreviewError."""
        try:
            layout = compile_layout({**idle, "render_mode": mode}, assets_dir=str(assets_dir))
        except LayoutError as e:
            raise PreviewError("layout does not compile: " + "; ".join(e.problems[:3])) from None
        live = {**sample, "epoch": int(time.time() if now is None else now), "tz": tz}   # the clock is live, the rest is sample data
        try:
            text = sample_lines(idle, live)
        except ValueError as e:
            raise PreviewError(f"sample-data.json: {e}") from None
        exe = self._exe(Path(src_bin))
        with tempfile.TemporaryDirectory(prefix="inkterface-idle-") as work:
            lay, txt, png = (os.path.join(work, n) for n in ("layout.bin", "sample.txt", "out.png"))
            with open(lay, "wb") as f:
                f.write(layout)
            with open(txt, "w", encoding="utf-8") as f:
                f.write(text)
            try:
                r = subprocess.run([str(exe), lay, txt, png], capture_output=True, timeout=TIMEOUT_S, cwd=work, env={},
                                   stdin=subprocess.DEVNULL, check=False)
            except subprocess.TimeoutExpired:
                raise PreviewError("idle preview timed out") from None
            except OSError as e:
                log.warning("idle renderer did not start: %s", e.strerror or type(e).__name__)
                raise PreviewError("idle preview renderer cannot start") from None
            if r.returncode != 0:
                log.warning("idle renderer exited with %s", r.returncode)   # stderr stays out of logs and results
                raise PreviewError(f"idle preview failed (renderer exit {r.returncode})")
            try:
                if os.path.getsize(png) > MAX_PNG:
                    raise PreviewError("idle preview failed (unexpected output)")
                with Image.open(png) as im:
                    im.load()
                    if im.size != SIZE:
                        raise PreviewError("idle preview failed (unexpected output size)")
                    img = im.convert("1" if mode == "mono" else "L")
            except PreviewError:
                raise
            except (OSError, ValueError, Image.DecompressionBombError):
                raise PreviewError("idle preview failed (unreadable output)") from None
        buf = io.BytesIO()
        img.save(buf, "PNG", optimize=True)
        return buf.getvalue()
