"""ILY1 idle-layout compiler: the `idle` section of a screen.json -> byte-exact wire.

compile_layout(idle_dict, *, assets_dir=None) -> bytes

Must agree byte for byte with firmware/src/ee05/idle_layout.cpp::decodeLayout (there is
none yet; decode.py is the oracle). Deterministic: identical input -> identical bytes.

Rejections raise LayoutError listing EVERY problem, each with a json path like
`idle.widgets[3].font: ...`. The compiler resolves source names to indices; the panel
never sees names.

CLI: python -m inkterface_core.idle.compile screen.json|folder -o out.bin
"""
from __future__ import annotations

import json
import os
import re
import struct
import sys
import zlib

from . import fields as F


class LayoutError(ValueError):
    """Carries every problem found, one per line."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = list(problems)
        super().__init__("\n".join(self.problems))


# --- small helpers ----------------------------------------------------------
class _Pool:
    """String pool with dedupe. offset 0 is the empty string."""

    def __init__(self) -> None:
        self.buf = bytearray(b"\x00")  # pool[0] = 0
        self._by = {b"": 0}

    def add(self, raw: bytes) -> int:
        if raw in self._by:
            return self._by[raw]
        off = len(self.buf)
        self.buf += raw + b"\x00"
        self._by[raw] = off
        return off


_UNIT_RE = re.compile(r"\{:\.(\d)f\}|\{:d\}|\{\}")
_TOKEN_RE = re.compile(r"\{\{|\}\}|\{([^}]*)\}")


def _split_binding(expr: str):
    """Split a screen.json binding into (source, path, idx-or-None).

    The optional `[n]` index attaches to an array segment anywhere in the path, e.g.
    `wx.forecast[0].max` -> ('wx', 'forecast.max', 0). Returns None on a parse failure.
    """
    m = re.match(r"^([A-Za-z_][\w-]*)\.(.+)$", expr)
    if not m:
        return None
    src = m.group(1)
    rest = m.group(2)
    idx = None
    im = re.search(r"\[(\d+)\]", rest)
    if im:
        idx = int(im.group(1))
        rest = rest[: im.start()] + rest[im.end():]
    if "[" in rest or "]" in rest:
        return None
    return src, rest, idx

_VALID_SRC_KEYS = {"type", "tz", "location", "units", "appid", "url", "pointer", "poll_s"}
_WIDGET_COMMON = {"type", "x", "y", "w", "h", "font", "align", "valign", "color",
                  "border", "invert"}
_WIDGET_EXTRA = {
    "text": {"text", "wrap", "ellipsis", "max_lines"},
    "number": {"bind", "format", "unit"},
    "bar": {"bind", "range", "orientation"},
    "icon": {"bind", "set", "name"},
    "image": {"src"},
    "clock": {"bind", "format"},
}
_TOP_KEYS = {"render_mode", "refresh_interval_s", "full_refresh_every", "dark",
             "sources", "widgets"}


def _encode_template(text: str, src_names: dict, prob: list, path: str) -> bytes | None:
    """Turn a template string into pool bytes: literals + 5-byte tokens.

    `{{`/`}}` -> literal braces. `{src.path[n]:.Nf}` -> token. Returns None (and
    appends to prob) on any failure, so the caller can keep collecting problems.
    """
    out = bytearray()
    ok = True
    i = 0
    n = len(text)
    while i < n:
        m = _TOKEN_RE.match(text, i)
        if not m:
            ch = text[i]
            b = _literal_byte(ch, prob, path)
            if b is None:
                ok = False
            else:
                out += bytes([b])
            i += 1
            continue
        tok = m.group(0)
        i = m.end()
        if tok == "{{":
            out += b"{"
            continue
        if tok == "}}":
            out += b"}"
            continue
        expr = m.group(1)
        enc = _encode_token(expr, src_names, prob, path)
        if enc is None:
            ok = False
        else:
            out += enc
    return bytes(out) if ok else None


def _literal_byte(ch: str, prob: list, path: str) -> int | None:
    if ch == "\u00b0":
        return F.CH_DEGREE
    o = ord(ch)
    if 0x20 <= o <= 0x7E:
        return o
    prob.append(f"{path}: character {ch!r} (U+{o:04X}) not allowed in a string")
    return None


def _encode_token(expr: str, src_names: dict, prob: list, path: str) -> bytes | None:
    # strip an optional ":.Nf" format spec
    dec_s = None
    fm = re.search(r":\.(\d)f$", expr)
    if fm:
        dec_s = fm.group(1)
        expr = expr[: fm.start()]
    parts = _split_binding(expr)
    if parts is None:
        prob.append(f"{path}: cannot parse template reference {{{expr}}}")
        return None
    sname, fpath, idx_s = parts
    if sname not in src_names:
        prob.append(f"{path}: unknown source '{sname}' in template")
        return None
    src_i, stype = src_names[sname]
    fi = F.find_path(stype, fpath)
    if fi is None:
        prob.append(f"{path}: unknown field '{fpath}' for source '{sname}'")
        return None
    idx = idx_s if idx_s is not None else 0
    if fi.count == 0:
        if idx_s is not None:
            prob.append(f"{path}: field '{fpath}' is not indexed")
            return None
    elif not (0 <= idx < fi.count):
        prob.append(f"{path}: index {idx} out of range for '{fpath}' (0..{fi.count - 1})")
        return None
    if fi.kind == "T":
        prob.append(f"{path}: time field '{fpath}' cannot be used in a template")
        return None
    if fi.kind in ("S",):
        if dec_s is not None:
            prob.append(f"{path}: string field '{fpath}' takes no decimals")
            return None
        spec = ord("D")
    else:  # N or A
        spec = ord(dec_s) if dec_s is not None else ord("D")
        if dec_s is not None and not (0 <= int(dec_s) <= 6):
            prob.append(f"{path}: decimals {dec_s} out of 0..6")
            return None
    return bytes([F.TOK, src_i + 1, fi.id + 1, idx + 1, spec])


def _encode_plain(text: str, prob: list, path: str) -> bytes | None:
    out = bytearray()
    ok = True
    for ch in text:
        b = _literal_byte(ch, prob, path)
        if b is None:
            ok = False
        else:
            out += bytes([b])
    return bytes(out) if ok else None


def _check_font_chars(raw: bytes, fid: int, prob: list, path: str) -> None:
    info = F.FONTS.get(fid)
    if info is None:
        return
    i = 0
    n = len(raw)
    while i < n:
        b = raw[i]
        if b == F.TOK:
            i += 5
            continue
        ch = "\u00b0" if b == F.CH_DEGREE else chr(b)
        if ch not in info.charset:
            prob.append(f"{path}: character {ch!r} is not in font '{info.style}-{info.size}'")
        i += 1


_CLOCK_DIRECTIVES = set("HMSIpAadeBbmyYj%")


def _check_clock_format(fmt: str, prob: list, path: str) -> None:
    i = 0
    n = len(fmt)
    while i < n:
        if fmt[i] == "%":
            if i + 1 >= n:
                prob.append(f"{path}: trailing '%' in clock format")
                return
            d = fmt[i + 1]
            if d not in _CLOCK_DIRECTIVES:
                prob.append(f"{path}: unknown clock directive %{d}")
            i += 2
        else:
            i += 1


def _check_clock_font_chars(fmt: str, fid: int, prob: list, path: str) -> None:
    """Literal (non-directive) characters of a clock format must be in the font.

    Ambiguity resolved: a `%X` directive expands to digits / day / month names the
    panel renders, so the directive letters themselves are NOT charset-checked; only
    the literal separators between them are. (A digits-only font therefore accepts
    `%H:%M` but rejects a literal letter.)
    """
    info = F.FONTS.get(fid)
    if info is None:
        return
    i = 0
    n = len(fmt)
    while i < n:
        if fmt[i] == "%":
            i += 2
            continue
        ch = fmt[i]
        if ch not in info.charset:
            prob.append(f"{path}: literal character {ch!r} is not in font "
                        f"'{info.style}-{info.size}'")
        i += 1


# --- source compilation -----------------------------------------------------
def _compile_sources(sources: dict, pool: _Pool, prob: list) -> tuple[list, dict]:
    """Returns (wire source dicts, {name: (index, type)}). Order = sources dict order."""
    out = []
    names: dict = {}
    if not isinstance(sources, dict):
        prob.append("idle.sources: must be an object")
        return out, names
    if len(sources) > F.MAX_SOURCES:
        prob.append(f"idle.sources: {len(sources)} sources > {F.MAX_SOURCES}")
    for i, (name, spec) in enumerate(sources.items()):
        p = f"idle.sources.{name}"
        if not isinstance(spec, dict):
            prob.append(f"{p}: must be an object")
            continue
        unknown = set(spec) - _VALID_SRC_KEYS
        if unknown:
            prob.append(f"{p}: unknown keys {sorted(unknown)}")
        stype_name = spec.get("type")
        stype = F.SRC_TYPE_BY_NAME.get(stype_name)
        if stype is None:
            prob.append(f"{p}.type: unknown source type {stype_name!r}")
            continue
        names[name] = (i, stype)
        sflags = 0
        poll = int(spec.get("poll_s", 0) or 0)
        s1 = F.NO_STR
        s2 = F.NO_STR
        if stype == F.SRC_CLOCK:
            if "tz" in spec and spec["tz"] not in (None, "", "auto"):
                b = _encode_plain(str(spec["tz"]), prob, f"{p}.tz")
                if b is not None and len(b) > F.CLOCK_TZ_MAX:
                    prob.append(f"{p}.tz: longer than {F.CLOCK_TZ_MAX} chars")
                elif b is not None:
                    s1 = pool.add(b)
            poll = 0  # clock ignores poll
        elif stype == F.SRC_WEATHER:
            units = spec.get("units", "")
            sflags = {"": 0, "metric": 1, "imperial": 2}.get(units)
            if sflags is None:
                prob.append(f"{p}.units: must be metric or imperial")
                sflags = 0
            loc = spec.get("location")
            if loc not in (None, "", "auto"):
                if not F.LATLON_RE.fullmatch(str(loc)):
                    prob.append(f"{p}.location: must be 'lat,lon'")
                else:
                    b = _encode_plain(str(loc), prob, f"{p}.location")
                    if b is not None:
                        s1 = pool.add(b)
        elif stype == F.SRC_STEAM:
            appid = spec.get("appid")
            if appid not in (None, ""):
                if not (str(appid).isascii() and str(appid).isdigit()) or len(str(appid)) > F.STEAM_APPID_MAX:
                    prob.append(f"{p}.appid: must be a decimal appid of 1..{F.STEAM_APPID_MAX} digits")
                else:
                    s1 = pool.add(str(appid).encode("ascii"))
        elif stype == F.SRC_HTTP_JSON:
            url = spec.get("url")
            if not url:
                prob.append(f"{p}.url: required for http_json")
            else:
                url = str(url)
                if not url.startswith("https://"):
                    prob.append(f"{p}.url: must be https://")
                elif not (F.HTTP_URL_MIN <= len(url) <= F.HTTP_URL_MAX):
                    prob.append(f"{p}.url: length {len(url)} out of {F.HTTP_URL_MIN}..{F.HTTP_URL_MAX}")
                else:
                    b = _encode_plain(url, prob, f"{p}.url")
                    if b is not None:
                        s1 = pool.add(b)
            ptr = spec.get("pointer", "")
            if ptr:
                ptr = str(ptr)
                if len(ptr) > F.HTTP_PTR_MAX:
                    prob.append(f"{p}.pointer: longer than {F.HTTP_PTR_MAX}")
                elif not ptr.startswith("/"):
                    prob.append(f"{p}.pointer: JSON pointer must start with '/'")
                else:
                    b = _encode_plain(ptr, prob, f"{p}.pointer")
                    if b is not None:
                        s2 = pool.add(b)
        if stype != F.SRC_CLOCK and poll and poll < F.MIN_POLL:
            prob.append(f"{p}.poll_s: {poll} < {F.MIN_POLL}")
        out.append({"type": stype, "flags": sflags, "poll": poll, "s1": s1, "s2": s2})
    return out, names


# --- widget compilation -----------------------------------------------------
def _resolve_bind(bind, names, prob, path):
    if not isinstance(bind, str):
        prob.append(f"{path}: bind must be a string")
        return None
    parts = _split_binding(bind)
    if parts is None:
        prob.append(f"{path}: cannot parse bind {bind!r}")
        return None
    sname, fpath, idx_s = parts
    if sname not in names:
        prob.append(f"{path}: unknown source '{sname}'")
        return None
    src_i, stype = names[sname]
    fi = F.find_path(stype, fpath)
    if fi is None:
        prob.append(f"{path}: unknown field '{fpath}' for source '{sname}'")
        return None
    idx = idx_s if idx_s is not None else 0
    if fi.count == 0:
        if idx_s is not None:
            prob.append(f"{path}: field '{fpath}' is not indexed")
            return None
    elif not (0 <= idx < fi.count):
        prob.append(f"{path}: index {idx} out of range (0..{fi.count - 1})")
        return None
    return src_i, fi, idx


def _blank_widget():
    return {"type": 0, "flags": 0, "x": 0, "y": 0, "w": 0, "h": 0, "font": 0,
            "align": 0, "color": 0, "border": 0, "text": F.NO_STR, "aux": F.NO_STR,
            "src": F.NO_SRC, "field": 0, "idx": 0, "p0": 0, "p1": 0, "max_lines": 0,
            "lo": 0, "hi": 0}


def _compile_widget(w, i, mode, names, pool, assets_dir, prob):
    path = f"idle.widgets[{i}]"
    if not isinstance(w, dict):
        prob.append(f"{path}: must be an object")
        return None
    wtype_name = w.get("type")
    tmap = {"text": F.W_TEXT, "number": F.W_NUMBER, "bar": F.W_BAR,
            "icon": F.W_ICON, "image": F.W_IMAGE, "clock": F.W_CLOCK}
    wtype = tmap.get(wtype_name)
    if wtype is None:
        prob.append(f"{path}.type: unknown widget type {wtype_name!r}")
        return None
    allowed = _WIDGET_COMMON | _WIDGET_EXTRA[wtype_name]
    unknown = set(w) - allowed
    if unknown:
        prob.append(f"{path}: unknown keys {sorted(unknown)}")

    out = _blank_widget()
    out["type"] = wtype

    # geometry
    for k in ("x", "y", "w", "h"):
        v = w.get(k)
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            prob.append(f"{path}.{k}: must be a non-negative integer")
            v = 0
        out[k] = v
    if out["w"] < 1 or out["h"] < 1:
        prob.append(f"{path}: w and h must be >= 1")
    if out["x"] + out["w"] > F.SCREEN_W:
        prob.append(f"{path}: extends past the {F.SCREEN_W}px edge (x+w={out['x']+out['w']})")
    if out["y"] + out["h"] > F.SCREEN_H:
        prob.append(f"{path}: extends past the {F.SCREEN_H}px edge (y+h={out['y']+out['h']})")

    halign = {"left": 0, "center": 1, "right": 2}.get(w.get("align", "left"))
    if halign is None:
        prob.append(f"{path}.align: must be left/center/right")
        halign = 0
    valign = {"top": 0, "middle": 1, "bottom": 2}.get(w.get("valign", "top"))
    if valign is None:
        prob.append(f"{path}.valign: must be top/middle/bottom")
        valign = 0
    out["align"] = halign | (valign << 2)

    color = w.get("color", 0)
    if not isinstance(color, int) or isinstance(color, bool) or not (0 <= color <= 3):
        prob.append(f"{path}.color: must be 0..3")
        color = 0
    if mode == F.RM_MONO and wtype in (F.W_TEXT, F.W_NUMBER, F.W_CLOCK) and color in (1, 2):
        prob.append(f"{path}.color: gray ({color}) on text is rejected in mono layouts")
    out["color"] = color

    border = w.get("border", 0)
    if not isinstance(border, int) or isinstance(border, bool) or not (0 <= border <= 4):
        prob.append(f"{path}.border: must be 0..4")
        border = 0
    out["border"] = border

    if w.get("invert"):
        out["flags"] |= F.WF_INVERT

    def font_id(default_name, allow_digits=True):
        fn = w.get("font", default_name)
        fi = F.font_by_name(fn)
        if fi is None:
            prob.append(f"{path}.font: unknown font {fn!r}")
            return 0
        return fi.id

    # per type ------------------------------------------------------------
    if wtype == F.W_TEXT:
        out["font"] = font_id("regular-28")
        tmpl = w.get("text")
        if not isinstance(tmpl, str):
            prob.append(f"{path}.text: required string template")
        else:
            raw = _encode_template(tmpl, names, prob, f"{path}.text")
            if raw is not None:
                _check_font_chars(raw, out["font"], prob, f"{path}.text")
                out["text"] = pool.add(raw)
        if w.get("wrap"):
            out["flags"] |= F.WF_WRAP
        if w.get("ellipsis", True) is False:
            out["flags"] |= F.WF_CLIP
        ml = w.get("max_lines", 0)
        if not isinstance(ml, int) or isinstance(ml, bool) or ml < 0 or ml > 255:
            prob.append(f"{path}.max_lines: must be 0..255")
            ml = 0
        out["max_lines"] = ml

    elif wtype == F.W_NUMBER:
        out["font"] = font_id("bold-28")
        r = _resolve_bind(w.get("bind"), names, prob, f"{path}.bind")
        if r:
            src_i, fi, idx = r
            if fi.kind not in ("N", "A"):
                prob.append(f"{path}.bind: field kind {fi.kind} not a number")
            out["src"], out["field"], out["idx"] = src_i, fi.id, idx
        out["p0"] = _number_decimals(w.get("format"), prob, f"{path}.format")
        unit = w.get("unit", "")
        if unit:
            b = _encode_plain(str(unit), prob, f"{path}.unit")
            if b is not None:
                _check_font_chars(b, out["font"], prob, f"{path}.unit")
                out["aux"] = pool.add(b)

    elif wtype == F.W_BAR:
        r = _resolve_bind(w.get("bind"), names, prob, f"{path}.bind")
        if r:
            src_i, fi, idx = r
            if fi.kind not in ("N", "A"):
                prob.append(f"{path}.bind: field kind {fi.kind} not a number")
            out["src"], out["field"], out["idx"] = src_i, fi.id, idx
        rng = w.get("range")
        if not (isinstance(rng, list) and len(rng) == 2
                and all(isinstance(v, int) and not isinstance(v, bool) for v in rng)):
            prob.append(f"{path}.range: must be [lo, hi] ints")
        elif rng[0] >= rng[1]:
            prob.append(f"{path}.range: lo ({rng[0]}) must be < hi ({rng[1]})")
        elif not (-32768 <= rng[0] <= 32767 and -32768 <= rng[1] <= 32767):
            prob.append(f"{path}.range: values must fit in i16")
        else:
            out["lo"], out["hi"] = rng
        ori = w.get("orientation", "horizontal")
        if ori not in ("horizontal", "vertical"):
            prob.append(f"{path}.orientation: horizontal or vertical")
        else:
            out["p0"] = 1 if ori == "vertical" else 0

    elif wtype == F.W_ICON:
        has_bind = "bind" in w
        has_name = "name" in w
        if has_bind and has_name:
            prob.append(f"{path}: icon takes bind OR name, not both")
        elif has_bind:
            if w.get("set", "weather") != "weather":
                prob.append(f"{path}.set: weather-code icons use set 'weather'")
            r = _resolve_bind(w.get("bind"), names, prob, f"{path}.bind")
            if r:
                src_i, fi, idx = r
                if fi.kind != "N":
                    prob.append(f"{path}.bind: weather icon needs a number (WMO code) field")
                out["src"], out["field"], out["idx"] = src_i, fi.id, idx
            out["p0"] = F.ICONS_WEATHER
        elif has_name:
            nm = w.get("name")
            if nm not in F.ICON_NAMES:
                prob.append(f"{path}.name: unknown icon {nm!r} (one of {F.ICON_NAMES})")
            else:
                out["p0"] = F.ICONS_NAMED
                out["p1"] = F.ICON_NAMES.index(nm)
        else:
            prob.append(f"{path}: icon needs bind or name")

    elif wtype == F.W_IMAGE:
        src = w.get("src")
        if not isinstance(src, str) or not src:
            prob.append(f"{path}.src: required image filename")
        elif assets_dir is None:
            prob.append(f"{path}.src: no assets_dir provided to resolve {src!r}")
        else:
            bmp, two = _load_image(src, assets_dir, out["w"], out["h"], mode, prob, path)
            if bmp is not None:
                if two:
                    out["flags"] |= F.WF_IMG_2BPP
                out["aux"] = pool.add(bmp)  # raw bitmap bytes in the pool (may contain 00)

    elif wtype == F.W_CLOCK:
        out["font"] = font_id("bold-160")
        fmt = w.get("format", "%H:%M")
        if not isinstance(fmt, str):
            prob.append(f"{path}.format: must be a strftime string")
        else:
            _check_clock_format(fmt, prob, f"{path}.format")
            b = _encode_plain(fmt, prob, f"{path}.format")
            if b is not None:
                _check_clock_font_chars(fmt, out["font"], prob, f"{path}.format")
                out["text"] = pool.add(b)
        bind = w.get("bind")
        if bind is None:
            # default: first clock source's .now
            clk = _first_clock(names)
            if clk is None:
                prob.append(f"{path}.bind: no clock source to default to")
            else:
                out["src"], out["field"], out["idx"] = clk, 0, 0
        else:
            r = _resolve_bind(bind, names, prob, f"{path}.bind")
            if r:
                src_i, fi, idx = r
                if fi.kind != "T":
                    prob.append(f"{path}.bind: clock needs a time field")
                out["src"], out["field"], out["idx"] = src_i, fi.id, idx

    return out


def _number_decimals(fmt, prob, path):
    if fmt is None:
        return F.DECIMALS_DEFAULT
    if not isinstance(fmt, str):
        prob.append(f"{path}: must be a format string")
        return F.DECIMALS_DEFAULT
    m = _UNIT_RE.fullmatch(fmt)
    if not m:
        prob.append(f"{path}: unsupported number format {fmt!r} (use '{{:.Nf}}','{{:d}}','{{}}')")
        return F.DECIMALS_DEFAULT
    if m.group(1) is not None:  # {:.Nf}
        return int(m.group(1))
    if fmt == "{:d}":
        return 0
    return F.DECIMALS_DEFAULT  # {}


def _first_clock(names):
    for name, (i, stype) in names.items():
        if stype == F.SRC_CLOCK:
            return i
    return None


def _load_image(src, assets_dir, w, h, mode, prob, path):
    p = os.path.join(assets_dir, src)
    if ".." in src.split(os.sep) or os.path.isabs(src) or "\\" in src:
        prob.append(f"{path}.src: unsafe path {src!r}")
        return None, False
    if not os.path.isfile(p):
        prob.append(f"{path}.src: file not found: {src}")
        return None, False
    try:
        from PIL import Image
    except ImportError:
        prob.append(f"{path}.src: Pillow required to compile image widgets")
        return None, False
    try:
        im = Image.open(p).convert("L").resize((w, h))
    except Exception as e:  # noqa: BLE001 - surfaced as a layout problem
        prob.append(f"{path}.src: cannot read image: {e}")
        return None, False
    two = mode == F.RM_GRAY4
    if two:
        return _pack_gray4(im, w, h), True
    return _pack_mono(im, w, h), False


def _pack_mono(im, w, h):
    # 1bpp, MSB first, 1 = black. Floyd-Steinberg dither for tone.
    bw = im.convert("1")  # Pillow error-diffusion dither
    px = bw.load()
    stride = (w + 7) // 8
    out = bytearray(stride * h)
    for y in range(h):
        for x in range(w):
            if px[x, y] == 0:  # black
                out[y * stride + (x >> 3)] |= 0x80 >> (x & 7)
    return bytes(out)


def _pack_gray4(im, w, h):
    # 2bpp, 4 levels, value 0 black .. 3 white, MSB-first packing of 2-bit units.
    px = im.load()
    stride = (w + 3) // 4
    out = bytearray(stride * h)
    for y in range(h):
        for x in range(w):
            lvl = px[x, y] * 3 // 255  # 0..3, 0 black .. 3 white
            shift = 6 - 2 * (x & 3)
            out[y * stride + (x >> 2)] |= lvl << shift
    return bytes(out)


# --- top level ---------------------------------------------------------------
def compile_layout(idle: dict, *, assets_dir: str | None = None) -> bytes:
    prob: list[str] = []
    if not isinstance(idle, dict):
        raise LayoutError(["idle: must be an object"])

    unknown = set(idle) - _TOP_KEYS
    if unknown:
        prob.append(f"idle: unknown keys {sorted(unknown)}")

    mode_name = idle.get("render_mode")
    if mode_name == "mono":
        mode = F.RM_MONO
    elif mode_name == "gray4":
        mode = F.RM_GRAY4
    else:
        prob.append("idle.render_mode: must be 'mono' or 'gray4'")
        mode = F.RM_MONO

    default_refresh = 60 if mode == F.RM_MONO else 900
    refresh_s = idle.get("refresh_interval_s", default_refresh)
    if not isinstance(refresh_s, int) or isinstance(refresh_s, bool) or not (1 <= refresh_s <= 65535):
        prob.append("idle.refresh_interval_s: must be 1..65535")
        refresh_s = default_refresh
    full_every = idle.get("full_refresh_every", 60)
    if not isinstance(full_every, int) or isinstance(full_every, bool) or not (1 <= full_every <= 65535):
        prob.append("idle.full_refresh_every: must be 1..65535")
        full_every = 60
    dark = bool(idle.get("dark", False))

    pool = _Pool()
    sources = idle.get("sources", {})
    wire_sources, names = _compile_sources(sources, pool, prob)

    widgets_in = idle.get("widgets")
    wire_widgets = []
    if not isinstance(widgets_in, list):
        prob.append("idle.widgets: must be an array")
        widgets_in = []
    if len(widgets_in) > F.MAX_WIDGETS:
        prob.append(f"idle.widgets: {len(widgets_in)} widgets > {F.MAX_WIDGETS}")
    for i, w in enumerate(widgets_in):
        cw = _compile_widget(w, i, mode, names, pool, assets_dir, prob)
        if cw is not None:
            wire_widgets.append(cw)

    if prob:
        raise LayoutError(prob)

    blob = _serialize(mode, refresh_s, full_every, dark, wire_sources, wire_widgets, pool)
    if len(blob) > F.MAX_LAYOUT_BYTES:
        raise LayoutError([f"idle: compiled layout {len(blob)} bytes > {F.MAX_LAYOUT_BYTES}"])
    return blob


def _serialize(mode, refresh_s, full_every, dark, sources, widgets, pool) -> bytes:
    flags = F.LF_DARK if dark else 0
    header = struct.pack(
        "<4sBBHHBBBBH", F.LAYOUT_MAGIC, F.LAYOUT_VER, mode, refresh_s, full_every,
        flags, len(sources), len(widgets), 0, len(pool.buf))
    body = bytearray(header)
    for s in sources:
        body += struct.pack("<BBHHH", s["type"], s["flags"], s["poll"], s["s1"], s["s2"])
    for w in widgets:
        body += struct.pack(
            "<BBHHHHBBBBHHBBBBBBhh",
            w["type"], w["flags"], w["x"], w["y"], w["w"], w["h"], w["font"],
            w["align"], w["color"], w["border"], w["text"], w["aux"],
            w["src"], w["field"], w["idx"], w["p0"], w["p1"], w["max_lines"],
            w["lo"], w["hi"])
    body += pool.buf
    body += struct.pack("<I", zlib.crc32(bytes(body)) & 0xFFFFFFFF)
    return bytes(body)


# --- CLI --------------------------------------------------------------------
def _idle_of(path: str) -> tuple[dict, str | None]:
    if os.path.isdir(path):
        sj = os.path.join(path, "screen.json")
        assets = os.path.join(path, "assets")
    else:
        sj = path
        assets = os.path.join(os.path.dirname(os.path.abspath(path)), "assets")
    with open(sj, encoding="utf-8") as fh:
        doc = json.load(fh)
    idle = doc.get("idle") if isinstance(doc, dict) else None
    if idle is None:
        raise SystemExit(f"{sj}: no 'idle' section")
    return idle, assets if os.path.isdir(assets) else None


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "-o" not in argv or len(argv) < 3:
        print("usage: python -m inkterface_core.idle.compile screen.json|folder -o out.bin",
              file=sys.stderr)
        return 2
    oi = argv.index("-o")
    out_path = argv[oi + 1]
    src = argv[0] if oi != 0 else argv[2]
    idle, assets = _idle_of(src)
    try:
        blob = compile_layout(idle, assets_dir=assets)
    except LayoutError as e:
        for line in e.problems:
            print(line, file=sys.stderr)
        return 1
    with open(out_path, "wb") as fh:
        fh.write(blob)
    print(f"{out_path}: {len(blob)} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
