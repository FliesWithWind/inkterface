"""Widget library. Each widget declares its options (used by the validator and the docs) and draws
onto its own w x h 1-bit tile; the renderer pastes the tile, so nothing can draw outside its box."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from .. import tz
from . import data as D
from .fonts import get_font, line_height, parse_font
from .icons import ICONS, draw_icon

MAX_ASSET_BYTES = 2 * 1024 * 1024   # images shipped in a package
MAX_BOUND_BYTES = 8 * 1024 * 1024   # images named by host data (Steam box art)
MAX_IMAGE_PIXELS = 16_000_000
ELLIPSIS = "…"


@dataclass(frozen=True)
class Opt:
    kind: str                      # int num bool str enum font range
    default: Any = None
    values: tuple = ()             # enum members
    min: float | None = None
    max: float | None = None
    templated: bool = False        # str option accepting {source} refs
    required: bool = False


@dataclass
class Ctx:
    img: Image.Image
    d: ImageDraw.ImageDraw
    w: int
    h: int
    ink: int
    paper: int
    snap: D.DataSnapshot
    assets_dir: Path | None = None


# ---- shared helpers -----------------------------------------------------------------------

_NUM_FMT = re.compile(r"([^{}]*)\{(?::([^{}]*))?\}([^{}]*)")


def parse_num_format(fmt: str) -> tuple[str, str, str] | None:
    """'{:.1f} x' -> ('', '.1f', ' x'). One field, no attribute/index access."""
    m = _NUM_FMT.fullmatch(fmt)
    return (m.group(1), m.group(2) or "", m.group(3)) if m else None


def fmt_number(v: float, fmt: str | None, precision: int) -> str | None:
    try:
        if fmt:
            pre, spec, post = parse_num_format(fmt)  # type: ignore[misc]
            return pre + format(v, spec) + post
        s = f"{v:.{precision}f}"
    except (ValueError, TypeError, OverflowError):
        return None
    return s[1:] if re.fullmatch(r"-0(\.0+)?", s) else s


def fmt_duration(seconds: float) -> str:
    s = max(0, int(seconds))
    h, rest = divmod(s, 3600)
    m = rest // 60
    return f"{h}h {m:02d}m" if h else (f"{m}m" if m else f"{s}s")


def ellipsize(text: str, font, width: float) -> str:
    """Shorten `text` so that it plus a trailing ellipsis fits; empty if even that cannot."""
    text = text.rstrip()
    while text and font.getlength(text + ELLIPSIS) > width:
        text = text[:-1].rstrip()
    return text + ELLIPSIS if font.getlength(text + ELLIPSIS) <= width else ""


def wrap_lines(text: str, font, width: float) -> list[str]:
    lines: list[str] = []
    for para in text.split("\n"):
        cur = ""
        for word in para.split(" "):
            cand = f"{cur} {word}" if cur else word
            if font.getlength(cand) <= width:
                cur = cand
                continue
            if cur:
                lines.append(cur)
            while len(word) > 1 and font.getlength(word) > width:
                n = next((n for n in range(len(word) - 1, 0, -1) if font.getlength(word[:n]) <= width), 1)
                lines.append(word[:n])
                word = word[n:]
            cur = word
        lines.append(cur)
    return lines


def _x_for(align: str, box_x: int, box_w: int, text_w: float) -> int:
    if align == "center":
        return box_x + int((box_w - text_w) // 2)
    return box_x + int(box_w - text_w) if align == "right" else box_x


def _y_for(valign: str, box_y: int, box_h: int, block_h: int) -> int:
    if valign == "middle":
        return box_y + (box_h - block_h) // 2
    return box_y + box_h - block_h if valign == "bottom" else box_y


def draw_value(ctx: Ctx, box, text: str, font_spec: str, unit: str, align: str, valign: str) -> None:
    """Value plus smaller unit on one baseline; shrinks the font until it fits the box width."""
    bx, by, bw, bh = box
    style, size = parse_font(font_spec)
    while True:
        font = get_font(f"{style}-{size}")
        gap = max(2, size // 8)
        ufont = get_font(f"regular-{max(6, round(size * 0.55))}") if unit else None
        total = font.getlength(text) + (gap + ufont.getlength(unit) if unit else 0)
        if total <= bw or size <= 8:
            break
        size = max(8, size - 2)
    x = _x_for(align, bx, bw, total)
    y = _y_for(valign, by, bh, line_height(font))
    base = y + font.getmetrics()[0]
    ctx.d.text((x, base), text, font=font, fill=ctx.ink, anchor="ls")
    if unit:
        ctx.d.text((x + font.getlength(text) + gap, base), unit, font=ufont, fill=ctx.ink, anchor="ls")


def placeholder(ctx: Ctx, box=None, font_spec: str = "regular-14") -> None:
    bx, by, bw, bh = box or (0, 0, ctx.w, ctx.h)
    draw_value(ctx, (bx, by, bw, bh), D.PLACEHOLDER, font_spec, "", "center", "middle")


def _tz(name: str):
    """tzinfo for an IANA name, None if unknown. Works without the stdlib `zoneinfo` (see inkterface_core.tz)."""
    return tz.get_zone(name)


# ---- image loading ------------------------------------------------------------------------

def load_1bit(path: Path, w: int, h: int, fit: str, dither: str, max_bytes: int, paper: int) -> Image.Image | None:
    """Open, scale and dither an image into a w x h 1-bit tile. None if unreadable/oversized."""
    try:
        if path.stat().st_size > max_bytes:
            return None
        with Image.open(path) as im:
            if im.width * im.height > MAX_IMAGE_PIXELS:
                return None
            rgba = im.convert("RGBA")
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        return None
    flat = Image.alpha_composite(Image.new("RGBA", rgba.size, "white"), rgba).convert("L")
    iw, ih = flat.size
    if fit == "fill":
        size = (w, h)
    else:
        s = (max if fit == "cover" else min)(w / iw, h / ih)
        size = (max(1, round(iw * s)), max(1, round(ih * s)))
    flat = flat.resize(size, Image.Resampling.LANCZOS)
    mode = Image.Dither.FLOYDSTEINBERG if dither == "floyd-steinberg" else Image.Dither.NONE
    bw = flat.convert("1", dither=mode)
    tile = Image.new("1", (w, h), paper)
    tile.paste(bw, ((w - size[0]) // 2, (h - size[1]) // 2))
    return tile.crop((0, 0, w, h))


# ---- widgets ------------------------------------------------------------------------------

class Widget:
    type = ""
    options: dict[str, Opt] = {}
    bind_kinds: frozenset[str] | None = None   # None: widget takes no `bind`
    bind_required = True

    def check(self, w: dict, path: str, v) -> None:
        """Cross-field validation hook; `v` offers err(path, msg)."""

    def draw(self, ctx: Ctx, o: dict) -> None:
        raise NotImplementedError


class Text(Widget):
    type = "text"
    options = {
        "text": Opt("str", required=True, templated=True),
        "font": Opt("font", "regular-16"),
        "align": Opt("enum", "left", ("left", "center", "right")),
        "valign": Opt("enum", "top", ("top", "middle", "bottom")),
        "wrap": Opt("bool", False),
        "ellipsis": Opt("bool", True),
        "max_lines": Opt("int", None, min=1, max=50),
    }

    def draw(self, ctx, o):
        font = get_font(o["font"])
        lh = line_height(font)
        text = D.render_template(o["text"], ctx.snap)
        lines = wrap_lines(text, font, ctx.w) if o["wrap"] else [text.replace("\n", " ")]
        cap = min(o["max_lines"] or 50, max(1, ctx.h // lh))
        if len(lines) > cap:
            lines = lines[:cap]
            lines[-1] = ellipsize(lines[-1] + " ", font, ctx.w) if o["ellipsis"] else lines[-1]
        elif lines and o["ellipsis"] and not o["wrap"] and font.getlength(lines[0]) > ctx.w:
            lines[0] = ellipsize(lines[0], font, ctx.w)
        y = _y_for(o["valign"], 0, ctx.h, lh * len(lines))
        for i, line in enumerate(lines):
            x = _x_for(o["align"], 0, ctx.w, font.getlength(line))
            ctx.d.text((x, y + i * lh), line, font=font, fill=ctx.ink)


class Number(Widget):
    type = "number"
    bind_kinds = frozenset({D.NUMBER})
    options = {
        "format": Opt("str"),
        "precision": Opt("int", 0, min=0, max=6),
        "unit": Opt("str", "", templated=True),
        "font": Opt("font", "bold-24"),
        "align": Opt("enum", "left", ("left", "center", "right")),
        "valign": Opt("enum", "middle", ("top", "middle", "bottom")),
    }

    def check(self, w, path, v):
        if w.get("format") is not None and isinstance(w["format"], str) and not parse_num_format(w["format"]):
            v.err(f"{path}.format", "must contain exactly one field like '{:.1f}'")

    def draw(self, ctx, o):
        val = ctx.snap.number(o["bind"])
        text = None if val is None else fmt_number(val, o["format"], o["precision"])
        unit = D.render_template(o["unit"], ctx.snap) if text is not None else ""
        draw_value(ctx, (0, 0, ctx.w, ctx.h), text if text is not None else D.PLACEHOLDER,
                   o["font"], unit, o["align"], o["valign"])


class Sparkline(Widget):
    type = "sparkline"
    bind_kinds = frozenset({D.SERIES})
    options = {
        "range": Opt("range"),
        "window": Opt("int", None, min=2, max=10000),
        "show_labels": Opt("bool", False),
        "label_format": Opt("str", "{:.0f}"),
        "thickness": Opt("int", 1, min=1, max=4),
        "fill": Opt("bool", False),
        "marker": Opt("bool", True),
    }

    def check(self, w, path, v):
        if isinstance(w.get("label_format"), str) and not parse_num_format(w["label_format"]):
            v.err(f"{path}.label_format", "must contain exactly one field like '{:.0f}'")

    def draw(self, ctx, o):
        series = ctx.snap.series(o["bind"])
        if series and o["window"]:
            series = series[-o["window"]:]
        if not series or len(series) < 2:
            return placeholder(ctx)
        lo, hi = o["range"] or (min(series), max(series))
        span = (hi - lo) or 1.0
        lfont = get_font("regular-12")
        left = 0
        labels = None
        if o["show_labels"]:
            labels = (fmt_number(hi, o["label_format"], 0) or "", fmt_number(lo, o["label_format"], 0) or "")
            left = int(max(lfont.getlength(s) for s in labels)) + 4
        x0, x1, y0, y1 = left, ctx.w - 3, 2, ctx.h - 3
        if x1 <= x0 or y1 <= y0:
            return placeholder(ctx)
        pts = [(x0 + round(i * (x1 - x0) / (len(series) - 1)),
                y1 - round(min(max((val - lo) / span, 0.0), 1.0) * (y1 - y0))) for i, val in enumerate(series)]
        if o["fill"]:   # sparse diagonal dots under the line
            for (xa, ya), (xb, yb) in zip(pts, pts[1:]):
                for x in range(xa, xb + 1):
                    top = round(ya + (yb - ya) * (x - xa) / ((xb - xa) or 1)) + 3
                    for y in range(top + (-(top + 2 * x)) % 4, y1 + 1, 4):
                        ctx.d.point((x, y), fill=ctx.ink)
        ctx.d.line(pts, fill=ctx.ink, width=o["thickness"], joint="curve")
        if o["marker"]:
            mx, my = pts[-1]
            ctx.d.rectangle((mx - 2, my - 2, mx + 2, my + 2), fill=ctx.ink)
        if labels:
            ctx.d.text((0, 0), labels[0], font=lfont, fill=ctx.ink)
            ctx.d.text((0, ctx.h - line_height(lfont)), labels[1], font=lfont, fill=ctx.ink)


class Bar(Widget):
    type = "bar"
    bind_kinds = frozenset({D.NUMBER})
    options = {
        "range": Opt("range", (0, 100)),
        "orientation": Opt("enum", "horizontal", ("horizontal", "vertical")),
    }

    def draw(self, ctx, o):
        ctx.d.rectangle((0, 0, ctx.w - 1, ctx.h - 1), outline=ctx.ink)
        val = ctx.snap.number(o["bind"])
        if val is None:
            return placeholder(ctx) if ctx.h >= 14 and ctx.w >= 24 else None
        lo, hi = o["range"]
        frac = min(max((val - lo) / (hi - lo), 0.0), 1.0)
        if o["orientation"] == "horizontal":
            n = round(frac * (ctx.w - 4))
            if n:
                ctx.d.rectangle((2, 2, 1 + n, ctx.h - 3), fill=ctx.ink)
        else:
            n = round(frac * (ctx.h - 4))
            if n:
                ctx.d.rectangle((2, ctx.h - 2 - n, ctx.w - 3, ctx.h - 3), fill=ctx.ink)


class Gauge(Widget):
    type = "gauge"
    bind_kinds = frozenset({D.NUMBER})
    options = {
        "range": Opt("range", (0, 100)),
        "start_angle": Opt("num", 135, min=-360, max=360),
        "sweep": Opt("num", 270, min=10, max=360),
        "thickness": Opt("int", 12, min=2, max=40),
        "format": Opt("str"),
        "precision": Opt("int", 0, min=0, max=6),
        "unit": Opt("str", "", templated=True),
        "font": Opt("font", "bold-32"),
    }

    def check(self, w, path, v):
        if isinstance(w.get("format"), str) and not parse_num_format(w["format"]):
            v.err(f"{path}.format", "must contain exactly one field like '{:.1f}'")

    def draw(self, ctx, o):
        s = min(ctx.w, ctx.h)
        r = s // 2 - 1
        cx, cy = ctx.w // 2, ctx.h // 2
        t = min(o["thickness"], r - 2)
        box = (cx - r, cy - r, cx + r, cy + r)
        inner = (cx - r + t, cy - r + t, cx + r - t, cy + r - t)
        a0, sweep = o["start_angle"], o["sweep"]
        val = ctx.snap.number(o["bind"])
        # channel outline: outer arc, inner arc, end caps
        ctx.d.arc(box, a0, a0 + sweep, fill=ctx.ink)
        ctx.d.arc(inner, a0, a0 + sweep, fill=ctx.ink)
        for a in (a0, a0 + sweep):
            c, sn = math.cos(math.radians(a)), math.sin(math.radians(a))
            ctx.d.line((cx + (r - t) * c, cy + (r - t) * sn, cx + r * c, cy + r * sn), fill=ctx.ink)
        text = None
        if val is not None:
            lo, hi = o["range"]
            frac = min(max((val - lo) / (hi - lo), 0.0), 1.0)
            if frac > 0:
                ctx.d.arc(box, a0, a0 + sweep * frac, fill=ctx.ink, width=t)
            text = fmt_number(val, o["format"], o["precision"])
        unit = D.render_template(o["unit"], ctx.snap) if text is not None else ""
        pad = t + 2
        draw_value(ctx, (cx - r + pad, cy - r + pad, 2 * (r - pad), 2 * (r - pad)),
                   text if text is not None else D.PLACEHOLDER, o["font"], unit, "center", "middle")


class Icon(Widget):
    type = "icon"
    bind_kinds = frozenset({D.STRING})
    bind_required = False
    options = {"name": Opt("enum", None, tuple(sorted(ICONS)))}

    def check(self, w, path, v):
        if "name" not in w and "bind" not in w:
            v.err(path, "needs 'name' or 'bind'")

    def draw(self, ctx, o):
        bound = ctx.snap.string(o["bind"]) if o.get("bind") else None
        name = bound if bound in ICONS else o["name"]
        if name not in ICONS:
            return placeholder(ctx)
        s = min(ctx.w, ctx.h)
        ctx.img.paste(draw_icon(name, s, ctx.ink, ctx.paper), ((ctx.w - s) // 2, (ctx.h - s) // 2))


class ImageWidget(Widget):
    type = "image"
    bind_kinds = frozenset({D.PATH})
    bind_required = False
    options = {
        "src": Opt("asset"),
        "fit": Opt("enum", "contain", ("contain", "cover", "fill")),
        "dither": Opt("enum", "floyd-steinberg", ("floyd-steinberg", "threshold")),
    }

    def check(self, w, path, v):
        if ("src" in w) == ("bind" in w):
            v.err(path, "needs exactly one of 'src' or 'bind'")

    def draw(self, ctx, o):
        tile = None
        if o.get("src"):
            if ctx.assets_dir:
                tile = load_1bit(ctx.assets_dir / o["src"], ctx.w, ctx.h, o["fit"], o["dither"],
                                 MAX_ASSET_BYTES, ctx.paper)
        else:
            tile = load_bound_image(ctx, o["bind"], ctx.w, ctx.h, o["fit"], o["dither"])
        if tile is None:
            return placeholder(ctx)
        ctx.img.paste(tile, (0, 0))


def load_bound_image(ctx: Ctx, bind: str, w: int, h: int, fit: str, dither: str) -> Image.Image | None:
    raw = ctx.snap.get(bind)
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        return None
    p = Path(raw)
    if not p.is_absolute():
        if ctx.snap.base_dir is None:
            return None
        p = Path(ctx.snap.base_dir) / p
    return load_1bit(p, w, h, fit, dither, MAX_BOUND_BYTES, ctx.paper)


class Clock(Widget):
    type = "clock"
    bind_kinds = frozenset({D.TIME})
    bind_required = False
    options = {
        "format": Opt("str", "%H:%M", templated=True),
        "timezone": Opt("str", "", templated=True),
        "font": Opt("font", "bold-48"),
        "align": Opt("enum", "center", ("left", "center", "right")),
        "valign": Opt("enum", "middle", ("top", "middle", "bottom")),
    }

    def check(self, w, path, v):
        tz = w.get("timezone")
        if isinstance(tz, str) and tz and "{" not in tz and _tz(tz) is None:
            v.err(f"{path}.timezone", f"unknown timezone '{tz}'")

    @staticmethod
    def text(o: dict, snap: D.DataSnapshot) -> str | None:
        """Formatted time, or None when the value or timezone is unusable."""
        bind = o.get("bind") or "time.now"
        dt = snap.time(bind)
        if dt is None:
            return None
        # zone: the option, else time.tz when showing time.now
        name = D.render_template(o["timezone"], snap) or (snap.string("time.tz") or "" if bind == "time.now" else "")
        try:
            if name:
                zone = _tz(name)
                if zone is None:
                    return None
                # naive input is already wall-clock time: attach the zone, do not shift it
                dt = dt.replace(tzinfo=zone) if dt.tzinfo is None else dt.astimezone(zone)
            return dt.strftime(D.render_template(o["format"], snap)) or None
        except (ValueError, OverflowError, OSError):
            return None

    def draw(self, ctx, o):
        draw_value(ctx, (0, 0, ctx.w, ctx.h), self.text(o, ctx.snap) or D.PLACEHOLDER, o["font"], "",
                   o["align"], o["valign"])


class NowPlaying(Widget):
    type = "now-playing"
    options = {
        "art_width": Opt("int", None, min=0, max=648),
        "font": Opt("font", "bold-28"),
        "show_achievement": Opt("bool", True),
    }
    GAP = 14

    def draw(self, ctx, o):
        s, ink = ctx.snap, ctx.ink
        art_w = min(o["art_width"] if o["art_width"] is not None else round(ctx.h * 2 / 3), ctx.w // 2)
        x = 0
        if art_w:
            tile = load_bound_image(ctx, "steam.now_playing.box_art_path", art_w, ctx.h, "contain", "floyd-steinberg")
            if tile is None:
                placeholder(ctx, (0, 0, art_w, ctx.h), "regular-24")
            else:
                ctx.img.paste(tile, (0, 0))
            ctx.d.rectangle((0, 0, art_w - 1, ctx.h - 1), outline=ink)
            x = art_w + self.GAP
        tw = ctx.w - x
        y = 0
        name = s.string("steam.now_playing.name") or D.PLACEHOLDER
        nf = get_font(o["font"])
        lines = wrap_lines(name, nf, tw)
        if len(lines) > 3:
            lines = lines[:3]
            lines[-1] = ellipsize(lines[-1] + " ", nf, tw)
        for line in lines:
            ctx.d.text((x, y), line, font=nf, fill=ink)
            y += line_height(nf)
        y += 8
        ctx.d.line((x, y, ctx.w - 1, y), fill=ink)
        y += 12
        label, big = get_font("regular-14"), get_font("bold-28")
        for title, key in (("SESSION", "steam.now_playing.session_minutes"), ("TOTAL", "steam.now_playing.total_minutes")):
            mins = s.number(key)
            ctx.d.text((x, y), title, font=label, fill=ink)
            y += line_height(label) + 2
            ctx.d.text((x, y), D.PLACEHOLDER if mins is None else fmt_duration(mins * 60), font=big, fill=ink)
            y += line_height(big) + 12
        if o["show_achievement"]:
            ctx.d.text((x, y), "LATEST ACHIEVEMENT", font=label, fill=ink)
            y += line_height(label) + 2
            af = get_font("regular-20")
            ach = wrap_lines(s.string("steam.now_playing.last_achievement") or D.PLACEHOLDER, af, tw)
            room = max(1, (ctx.h - y) // line_height(af))
            if len(ach) > room:
                ach = ach[:room]
                ach[-1] = ellipsize(ach[-1] + " ", af, tw)
            for line in ach:
                ctx.d.text((x, y), line, font=af, fill=ink)
                y += line_height(af)


WIDGETS: dict[str, Widget] = {w.type: w for w in (Text(), Number(), Sparkline(), Bar(), Gauge(), Icon(),
                                                    ImageWidget(), Clock(), NowPlaying())}

COMMON = {
    "id": Opt("str"),
    "invert": Opt("bool", False),
    "border": Opt("int", 0, min=0, max=4),
}
GEOMETRY = ("x", "y", "w", "h")


def resolve_options(widget: dict) -> dict:
    spec = WIDGETS[widget["type"]]
    out = {k: o.default for k, o in {**COMMON, **spec.options}.items()}
    out.update(widget)
    if spec.bind_kinds is not None:
        out.setdefault("bind", None)
    return out


def render_widget(widget: dict, snap: D.DataSnapshot, assets_dir: Path | None = None) -> Image.Image:
    """Draw one (validated) widget dict onto a fresh w x h 1-bit tile."""
    o = resolve_options(widget)
    ink, paper = (255, 0) if o["invert"] else (0, 255)
    tile = Image.new("1", (o["w"], o["h"]), paper)
    ctx = Ctx(tile, ImageDraw.Draw(tile), o["w"], o["h"], ink, paper, snap, assets_dir)
    WIDGETS[o["type"]].draw(ctx, o)
    if o["border"]:
        ctx.d.rectangle((0, 0, ctx.w - 1, ctx.h - 1), outline=ink, width=o["border"])
    return tile
