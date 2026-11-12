"""Screen + data snapshot + settings -> 648x480 1-bit image -> packed bytes (same layout as frame.py)."""
from __future__ import annotations

import logging
from collections.abc import Mapping

from PIL import Image, ImageDraw

from . import data as D
from .schema import HEIGHT, WIDTH, Screen, ScreenError
from .widgets import Ctx, placeholder, render_widget

log = logging.getLogger(__name__)
FRAME_BYTES = WIDTH // 8 * HEIGHT  # 38880


def render(screen: Screen, data: Mapping | None = None, settings: Mapping | None = None) -> Image.Image:
    """Render the active section. Deterministic. A widget that fails shows '--', never aborts the frame."""
    if screen.active is None:
        raise ScreenError(f"{screen.id}: has no 'active' section")
    snap = data if isinstance(data, D.DataSnapshot) else D.DataSnapshot(data)
    snap = snap.with_settings(screen.resolve_settings(settings))
    img = Image.new("1", (WIDTH, HEIGHT), 255)
    for i, w in enumerate(screen.active["widgets"]):
        try:
            tile = render_widget(w, snap, screen.assets_dir)
        except Exception:  # noqa: BLE001 - one broken widget must not blank the panel
            log.exception("%s: widget %d (%s) failed", screen.id, i, w["type"])
            tile = Image.new("1", (w["w"], w["h"]), 255)
            placeholder(Ctx(tile, ImageDraw.Draw(tile), w["w"], w["h"], 0, 255, snap))
        img.paste(tile, (w["x"], w["y"]))
    return img


def pack(img: Image.Image) -> bytes:
    """38880 bytes, row-major, MSB first, 1 = white (matches frame.render_test_frame)."""
    if img.mode != "1" or img.size != (WIDTH, HEIGHT):
        raise ValueError(f"need a {WIDTH}x{HEIGHT} 1-bit image, got {img.size} {img.mode}")
    return img.tobytes()


def unpack(frame: bytes) -> Image.Image:
    if len(frame) != FRAME_BYTES:
        raise ValueError(f"frame must be {FRAME_BYTES} bytes, got {len(frame)}")
    return Image.frombytes("1", (WIDTH, HEIGHT), frame)


def render_frame(screen: Screen, data: Mapping | None = None, settings: Mapping | None = None) -> bytes:
    return pack(render(screen, data, settings))
