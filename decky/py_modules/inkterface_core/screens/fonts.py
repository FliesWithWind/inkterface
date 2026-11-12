"""Bundled fonts: Inkterface Mono Regular + Bold = IBM Plex Mono without its hinting instructions (SIL OFL 1.1, see fonts/OFL.txt and
fonts/FONTLOG.txt). With no instructions FreeType uses its auto-hinter, which gives all glyphs of a size the same stem width."""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

FONT_DIR = Path(__file__).resolve().parent / "fonts"
FILES = {"regular": "InkterfaceMono-Regular.ttf", "bold": "InkterfaceMono-Bold.ttf"}
MIN_SIZE, MAX_SIZE = 6, 200
_SPEC = re.compile(r"(regular|bold)-(\d{1,3})")


def parse_font(spec: str) -> tuple[str, int]:
    """'bold-32' -> ('bold', 32). ValueError when malformed or out of range."""
    m = _SPEC.fullmatch(spec) if isinstance(spec, str) else None
    if not m or not MIN_SIZE <= int(m.group(2)) <= MAX_SIZE:
        raise ValueError(f"font must look like 'regular-16' or 'bold-32' (size {MIN_SIZE}-{MAX_SIZE})")
    return m.group(1), int(m.group(2))


@lru_cache(maxsize=64)
def get_font(spec: str) -> ImageFont.FreeTypeFont:
    style, size = parse_font(spec)
    return ImageFont.truetype(str(FONT_DIR / FILES[style]), size)


def line_height(font) -> int:
    asc, desc = font.getmetrics()
    return asc + desc
