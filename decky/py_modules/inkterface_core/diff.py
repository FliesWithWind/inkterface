"""Dirty-rectangle computation for 1-bit frames (packed 648x480, MSB first, 1 = black)."""
from __future__ import annotations

from .proto import FB_BYTES, HEIGHT, MAX_PAYLOAD, STRIDE, WIDTH

Rect = tuple[int, int, int, int]  # x, y, w, h in pixels; x and w multiples of 8

# Wire cost of one RECT besides pixels: 6 hdr + 4 crc + 8 rect header.
RECT_OVERHEAD = 18
MAX_ROWS_FULL_WIDTH = (MAX_PAYLOAD - 8) // STRIDE  # 50


def _row_span(a: bytes, b: bytes) -> tuple[int, int] | None:
    """First/last differing byte column of two equal-length rows, or None."""
    if a == b:
        return None
    lo = 0
    while a[lo] == b[lo]:
        lo += 1
    hi = len(a) - 1
    while a[hi] == b[hi]:
        hi -= 1
    return lo, hi


def diff_rects(prev: bytes, new: bytes) -> list[Rect]:
    """Dirty rects between two frames, each fitting one RECT frame.

    Greedy: walk changed rows top to bottom and grow the current rect while the
    bytes saved by merging beat the ~18-byte cost of an extra RECT. Not optimal,
    but never larger than the bounding box and cheap (pure Python, ~1 ms).
    Union of all rects covers every differing pixel (tests assert this).
    """
    if len(prev) != FB_BYTES or len(new) != FB_BYTES:
        raise ValueError(f"frames must be {FB_BYTES} bytes")
    spans: list[tuple[int, int, int]] = []  # (row, lo, hi)
    for y in range(HEIGHT):
        s = _row_span(prev[y * STRIDE : (y + 1) * STRIDE], new[y * STRIDE : (y + 1) * STRIDE])
        if s:
            spans.append((y, s[0], s[1]))
    rects: list[list[int]] = []  # [row0, row1_excl, lo, hi]
    for y, lo, hi in spans:
        if rects:
            r0, r1, rlo, rhi = rects[-1]
            nlo, nhi = min(rlo, lo), max(rhi, hi)
            merged = (y + 1 - r0) * (nhi - nlo + 1)
            separate = (r1 - r0) * (rhi - rlo + 1) + (hi - lo + 1) + RECT_OVERHEAD
            if merged <= separate:
                rects[-1] = [r0, y + 1, nlo, nhi]
                continue
        rects.append([y, y + 1, lo, hi])
    out: list[Rect] = []
    for r0, r1, lo, hi in rects:
        out += split_rect((lo * 8, r0, (hi - lo + 1) * 8, r1 - r0))
    return out


def split_rect(r: Rect) -> list[Rect]:
    """Split into horizontal bands so each RECT payload is <= MAX_PAYLOAD."""
    x, y, w, h = r
    rows = max(1, (MAX_PAYLOAD - 8) // (w // 8))
    return [(x, y + dy, w, min(rows, h - dy)) for dy in range(0, h, rows)]


def crop(fb: bytes, r: Rect) -> bytes:
    """Packed rows of rect r out of a full frame."""
    x, y, w, h = r
    c0, n = x // 8, w // 8
    return b"".join(fb[(y + i) * STRIDE + c0 : (y + i) * STRIDE + c0 + n] for i in range(h))


def blank() -> bytes:
    return bytes(FB_BYTES)  # all white (0 bits)


def pixel(fb: bytes, x: int, y: int) -> int:
    return (fb[y * STRIDE + x // 8] >> (7 - x % 8)) & 1


def from_image(img) -> bytes:
    """PIL image -> packed frame (1 = black). Convenience; needs Pillow at call time."""
    img = img.convert("1")
    if img.size != (WIDTH, HEIGHT):
        raise ValueError(f"image must be {WIDTH}x{HEIGHT}")
    return bytes(b ^ 0xFF for b in img.tobytes())  # PIL "1": bit 1 = white
