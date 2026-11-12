"""Shared helpers for the screen tests."""
import io

from PIL import Image


def valid_doc(**over):
    doc = {"schema": 1, "id": "demo", "name": "Demo", "version": "1.0.0", "author": "me",
           "active": {"widgets": [{"type": "text", "x": 0, "y": 0, "w": 100, "h": 20, "text": "hi"}]}}
    doc.update(over)
    return doc


def png_bytes(size=(8, 8), mode="L", color=0, fmt="PNG"):
    buf = io.BytesIO()
    Image.new(mode, size, color).save(buf, fmt)
    return buf.getvalue()


def ink(tile, box=None):
    """Number of black pixels in a 1-bit tile (optionally within box=(l, t, r, b))."""
    t = tile.crop(box) if box else tile
    return t.convert("L").tobytes().count(0)
