"""Built-in 1-bit icon set, drawn as vector primitives on a 24x24 grid and scaled to any size."""
from __future__ import annotations

import math

from PIL import Image, ImageDraw


class _Pen:
    """Scales 24-grid coordinates to the tile size."""

    def __init__(self, d: ImageDraw.ImageDraw, size: int, ink: int, paper: int):
        self.d, self.size, self.ink, self.paper = d, size, ink, paper
        self.lw = max(1, round(size / 12))

    def u(self, *c: float) -> tuple[int, ...]:
        return tuple(round(v * self.size / 24) for v in c)

    def line(self, *c):
        self.d.line(self.u(*c), fill=self.ink, width=self.lw)

    def box(self, *c, fill=False):
        self.d.rectangle(self.u(*c), fill=self.ink if fill else None, outline=self.ink, width=self.lw)

    def oval(self, *c, fill=False):
        self.d.ellipse(self.u(*c), fill=self.ink if fill else None, outline=self.ink, width=self.lw)

    def poly(self, pts):
        self.d.polygon([self.u(*p) for p in pts], fill=self.ink)


def _cpu(p: _Pen):
    p.box(5, 5, 19, 19)
    p.box(9, 9, 15, 15, fill=True)
    for v in (8, 12, 16):
        p.line(v, 2, v, 5), p.line(v, 19, v, 22), p.line(2, v, 5, v), p.line(19, v, 22, v)


def _gpu(p: _Pen):
    p.box(2, 6, 22, 17)
    p.oval(6, 8, 15, 15)
    p.box(17, 9, 20, 14, fill=True)
    p.line(4, 20, 20, 20)


def _thermometer(p: _Pen):
    p.box(10, 2, 14, 15)
    p.oval(7, 13, 17, 23, fill=True)
    p.line(12, 6, 12, 16)
    for y in (5, 8, 11):
        p.line(16, y, 19, y)


def _fan(p: _Pen):
    p.oval(2, 2, 22, 22)
    for a in (-15, 105, 225):
        p.d.pieslice(p.u(4, 4, 20, 20), a, a + 60, fill=p.ink)
    p.d.ellipse(p.u(10, 10, 14, 14), fill=p.paper, outline=p.ink)


def _bolt(p: _Pen):
    p.poly([(14, 1), (4, 14), (11, 14), (9, 23), (20, 9), (13, 9)])


def _gamepad(p: _Pen):
    p.d.rounded_rectangle(p.u(2, 7, 22, 18), radius=p.u(5)[0], outline=p.ink, width=p.lw)
    p.line(7, 10, 7, 15), p.line(4.5, 12.5, 9.5, 12.5)
    p.oval(14, 11, 16.5, 13.5, fill=True), p.oval(17.5, 11, 20, 13.5, fill=True)


def _sun(p: _Pen):
    p.oval(7, 7, 17, 17, fill=True)
    for i in range(8):
        a = math.radians(i * 45)
        p.line(12 + 7 * math.cos(a), 12 + 7 * math.sin(a), 12 + 10.5 * math.cos(a), 12 + 10.5 * math.sin(a))


def _cloud_shape(p: _Pen, dy: float = 0):
    p.d.ellipse(p.u(2, 10 + dy, 11, 19 + dy), fill=p.ink)
    p.d.ellipse(p.u(6, 5 + dy, 17, 16 + dy), fill=p.ink)
    p.d.ellipse(p.u(12, 9 + dy, 22, 19 + dy), fill=p.ink)
    p.d.rectangle(p.u(6, 13 + dy, 17, 19 + dy), fill=p.ink)


def _cloud(p: _Pen):
    _cloud_shape(p, 1)


def _rain(p: _Pen):
    _cloud_shape(p, -3)
    for x in (6, 11, 16):
        p.line(x + 1, 18, x - 1, 22)


def _clock(p: _Pen):
    p.oval(2, 2, 22, 22)
    p.line(12, 12, 12, 6)
    p.line(12, 12, 17, 14)


ICONS = {f.__name__[1:]: f for f in (_cpu, _gpu, _thermometer, _fan, _bolt, _gamepad, _sun, _cloud, _rain, _clock)}


def draw_icon(name: str, size: int, ink: int = 0, paper: int = 255) -> Image.Image:
    """Square 1-bit tile `size` px wide; KeyError for an unknown name."""
    fn = ICONS[name]
    tile = Image.new("1", (size, size), paper)
    fn(_Pen(ImageDraw.Draw(tile), size, ink, paper))
    return tile
