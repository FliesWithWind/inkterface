"""Strict ILY1 layout decoder: the Python oracle the firmware C++ decoder is
fuzz-compared against (firmware/src/ee05/idle_layout.cpp :: decodeLayout).

It re-validates every byte the panel would later trust, in the exact order of
idle_layout.h::DecodeError (length/magic/version/crc first, then header, sources,
widgets, strings, refs) so both decoders report the same FIRST error. Where
docs/idle-mode.md is silent the strictest sensible rule is chosen; those choices
are called out in comments and in the host report.

decode_layout(bytes) -> dict  (same shape compile.py's _to_dict round-trips to)
raises DecodeError(code, msg) with code in:
  TOO_SHORT TOO_BIG BAD_MAGIC BAD_VERSION BAD_CRC BAD_HEADER BAD_SOURCE
  BAD_WIDGET BAD_STRING BAD_REF
"""
from __future__ import annotations

import struct
import zlib

from . import fields as F


class DecodeError(Exception):
    def __init__(self, code: str, msg: str) -> None:
        super().__init__(f"{code}: {msg}")
        self.code = code
        self.msg = msg


_CODES = {
    "TOO_SHORT", "TOO_BIG", "BAD_MAGIC", "BAD_VERSION", "BAD_CRC",
    "BAD_HEADER", "BAD_SOURCE", "BAD_WIDGET", "BAD_STRING", "BAD_REF",
}


def _err(code: str, msg: str) -> DecodeError:
    assert code in _CODES, code
    return DecodeError(code, msg)


# ---------------------------------------------------------------------------
def _pool_str(pool: bytes, off: int) -> bytes:
    """Return bytes of a NUL-terminated string at `off` (NUL inside pool).

    Raises BAD_STRING on a missing terminator or an offset past the pool. The caller
    has already rejected NO_STR (absent) where absence is legal.
    """
    if off >= len(pool):
        raise _err("BAD_STRING", f"pool offset {off} past pool_len {len(pool)}")
    end = pool.find(0, off)
    if end < 0:
        raise _err("BAD_STRING", f"string at {off} has no NUL inside the pool")
    return pool[off:end]


def _check_string_bytes(raw: bytes, *, allow_tokens: bool, off: int) -> None:
    """Printable ASCII 0x20..0x7E, plus 0x7F (degree). 0x01 template tokens only
    where allowed (text/clock format strings). No other control bytes."""
    i = 0
    n = len(raw)
    while i < n:
        b = raw[i]
        if b == F.TOK:
            if not allow_tokens:
                raise _err("BAD_STRING", f"string at {off}: unexpected template token")
            # token: 01 | src+1 | field+1 | idx+1 | spec  (4 exempt bytes follow)
            if i + 4 >= n:
                raise _err("BAD_STRING", f"string at {off}: truncated template token")
            i += 5
            continue
        if 0x20 <= b <= 0x7F:
            i += 1
            continue
        raise _err("BAD_STRING", f"string at {off}: non-printable byte 0x{b:02x}")


_CLOCK_DIRECTIVES = set("HMSIpAadeBbmyYj%")


def _check_clock_format(raw: bytes, off: int) -> None:
    """strftime subset: %H %M %S %I %p %A %a %d %e %B %b %m %y %Y %j %%. A clock
    format must contain no template tokens (checked by _check_string_bytes already)."""
    i = 0
    n = len(raw)
    while i < n:
        b = raw[i]
        if b == ord("%"):
            if i + 1 >= n:
                raise _err("BAD_STRING", f"clock format at {off}: trailing '%'")
            d = chr(raw[i + 1])
            if d not in _CLOCK_DIRECTIVES:
                raise _err("BAD_STRING", f"clock format at {off}: unknown %{d}")
            i += 2
            continue
        i += 1


def _check_template_tokens(raw: bytes, off: int) -> None:
    """Validate every 01-token in a text template: src/field/idx exist, kind in N/S/A,
    spec is '0'..'6' or 'D' (only 'D' for S/A-string fields), idx in range."""
    i = 0
    n = len(raw)
    while i < n:
        if raw[i] != F.TOK:
            i += 1
            continue
        src1, field1, idx1, spec = raw[i + 1], raw[i + 2], raw[i + 3], raw[i + 4]
        i += 5
        # stored as +1 so a literal 0x00 never appears inside a token
        if src1 == 0 or field1 == 0 or idx1 == 0:
            raise _err("BAD_STRING", f"token at {off}: zero src/field/idx")
        # resolution against the actual sources happens in _resolve_token (BAD_REF);
        # here only the spec charset is a pure-string check.
        if not (0x30 <= spec <= 0x36 or spec == ord("D")):
            raise _err("BAD_STRING", f"token at {off}: bad spec byte 0x{spec:02x}")


def _font_charset_ok(fid: int, raw: bytes) -> bool:
    """Every literal byte (tokens skipped) is in the font charset. 0x7F = degree."""
    info = F.FONTS.get(fid)
    if info is None:
        return False
    cs = info.charset
    i = 0
    n = len(raw)
    while i < n:
        b = raw[i]
        if b == F.TOK:
            i += 5
            continue
        ch = "\u00b0" if b == F.CH_DEGREE else chr(b)
        if ch not in cs:
            return False
        i += 1
    return True


def _clock_font_charset_ok(fid: int, raw: bytes) -> bool:
    """Like _font_charset_ok but skips `%X` directive letters: they expand to glyphs the
    panel renders, so only literal separators are charset-checked (matches the compiler)."""
    info = F.FONTS.get(fid)
    if info is None:
        return False
    cs = info.charset
    i = 0
    n = len(raw)
    while i < n:
        if raw[i] == ord("%"):
            i += 2
            continue
        ch = "\u00b0" if raw[i] == F.CH_DEGREE else chr(raw[i])
        if ch not in cs:
            return False
        i += 1
    return True


# ---------------------------------------------------------------------------
def decode_layout(buf: bytes) -> dict:
    buf = bytes(buf)
    n = len(buf)

    # 1. length / magic / version / crc -------------------------------------
    if n < F.HEADER_LEN + 4:
        raise _err("TOO_SHORT", f"{n} bytes < header+crc ({F.HEADER_LEN + 4})")
    if n > F.MAX_LAYOUT_BYTES:
        raise _err("TOO_BIG", f"{n} > MAX_LAYOUT_BYTES ({F.MAX_LAYOUT_BYTES})")
    if buf[0:4] != F.LAYOUT_MAGIC:
        raise _err("BAD_MAGIC", f"magic {buf[0:4]!r} != {F.LAYOUT_MAGIC!r}")
    version = buf[4]
    if version != F.LAYOUT_VER:
        raise _err("BAD_VERSION", f"version {version} != {F.LAYOUT_VER}")
    want_crc = struct.unpack_from("<I", buf, n - 4)[0]
    got_crc = zlib.crc32(buf[: n - 4]) & 0xFFFFFFFF
    if want_crc != got_crc:
        raise _err("BAD_CRC", f"crc 0x{want_crc:08x} != computed 0x{got_crc:08x}")

    # 2. header -------------------------------------------------------------
    render_mode = buf[5]
    refresh_s = struct.unpack_from("<H", buf, 6)[0]
    full_every = struct.unpack_from("<H", buf, 8)[0]
    flags = buf[10]
    n_sources = buf[11]
    n_widgets = buf[12]
    reserved = buf[13]
    pool_len = struct.unpack_from("<H", buf, 14)[0]

    if render_mode not in (F.RM_MONO, F.RM_GRAY4):
        raise _err("BAD_HEADER", f"render_mode {render_mode}")
    if refresh_s == 0:
        raise _err("BAD_HEADER", "refresh_s 0 (must be 1..65535)")
    if full_every == 0:
        raise _err("BAD_HEADER", "full_every 0 (must be 1..65535)")
    if flags & ~F.LF_DARK:
        raise _err("BAD_HEADER", f"flags 0x{flags:02x}: reserved bits set")
    if reserved != 0:
        raise _err("BAD_HEADER", f"reserved byte 0x{reserved:02x} != 0")
    if n_sources > F.MAX_SOURCES:
        raise _err("BAD_HEADER", f"n_sources {n_sources} > {F.MAX_SOURCES}")
    if n_widgets > F.MAX_WIDGETS:
        raise _err("BAD_HEADER", f"n_widgets {n_widgets} > {F.MAX_WIDGETS}")

    expect = F.HEADER_LEN + F.SOURCE_LEN * n_sources + F.WIDGET_LEN * n_widgets + pool_len + 4
    if expect != n:
        raise _err("BAD_HEADER", f"length {n} != computed {expect}")

    src_base = F.HEADER_LEN
    wid_base = src_base + F.SOURCE_LEN * n_sources
    pool_base = wid_base + F.WIDGET_LEN * n_widgets
    pool = buf[pool_base : pool_base + pool_len]

    # pool[0] must be the empty string (offset 0 = "")
    if pool_len == 0 or pool[0] != 0:
        raise _err("BAD_STRING", "pool[0] must be NUL (offset 0 is the empty string)")

    # 3. sources ------------------------------------------------------------
    sources = []
    for i in range(n_sources):
        o = src_base + i * F.SOURCE_LEN
        stype, sflags, poll_s, s1, s2 = struct.unpack_from("<BBHHH", buf, o)
        src = _decode_source(stype, sflags, poll_s, s1, s2, pool, i)
        sources.append(src)

    # 4. widgets ------------------------------------------------------------
    widgets = []
    for i in range(n_widgets):
        o = wid_base + i * F.WIDGET_LEN
        widgets.append(_decode_widget(buf, o, pool, render_mode, pool_len, sources, i))

    return {
        "render_mode": "mono" if render_mode == F.RM_MONO else "gray4",
        "refresh_interval_s": refresh_s,
        "full_refresh_every": full_every,
        "dark": bool(flags & F.LF_DARK),
        "sources": sources,
        "widgets": widgets,
    }


def _decode_source(stype, sflags, poll_s, s1, s2, pool, i):
    name = F.SRC_NAME_BY_TYPE.get(stype)
    if name is None:
        raise _err("BAD_SOURCE", f"sources[{i}]: unknown type {stype}")
    out = {"type": name}

    if stype == F.SRC_CLOCK:
        if sflags != 0:
            raise _err("BAD_SOURCE", f"sources[{i}] clock: flags {sflags} must be 0")
        if s2 != F.NO_STR:
            raise _err("BAD_SOURCE", f"sources[{i}] clock: s2 must be absent")
        if s1 != F.NO_STR:
            tz = _pool_str(pool, s1)
            _check_string_bytes(tz, allow_tokens=False, off=s1)
            if len(tz) > F.CLOCK_TZ_MAX:
                raise _err("BAD_SOURCE", f"sources[{i}] clock: tz longer than {F.CLOCK_TZ_MAX}")
            out["tz"] = tz.decode("ascii")
    elif stype == F.SRC_WEATHER:
        if sflags & ~F.SF_UNITS_MASK:
            raise _err("BAD_SOURCE", f"sources[{i}] weather: flags 0x{sflags:02x}")
        if (sflags & F.SF_UNITS_MASK) == 3:
            raise _err("BAD_SOURCE", f"sources[{i}] weather: units value 3 invalid")
        if s2 != F.NO_STR:
            raise _err("BAD_SOURCE", f"sources[{i}] weather: s2 must be absent")
        units = sflags & F.SF_UNITS_MASK
        out["units"] = {0: "", 1: "metric", 2: "imperial"}[units]
        if s1 != F.NO_STR:
            loc = _pool_str(pool, s1)
            _check_string_bytes(loc, allow_tokens=False, off=s1)
            if not F.LATLON_RE.fullmatch(loc.decode("ascii")):
                raise _err("BAD_SOURCE", f"sources[{i}] weather: location must be 'lat,lon'")
            out["location"] = loc.decode("ascii")
    elif stype == F.SRC_STEAM:
        if sflags != 0:
            raise _err("BAD_SOURCE", f"sources[{i}] steam: flags {sflags} must be 0")
        if s2 != F.NO_STR:
            raise _err("BAD_SOURCE", f"sources[{i}] steam: s2 must be absent")
        if s1 != F.NO_STR:
            appid = _pool_str(pool, s1)
            _check_string_bytes(appid, allow_tokens=False, off=s1)
            txt = appid.decode("ascii")
            if not (txt.isascii() and txt.isdigit()) or len(txt) > F.STEAM_APPID_MAX:
                raise _err("BAD_SOURCE", f"sources[{i}] steam: appid '{txt}' not 1..{F.STEAM_APPID_MAX} digits")
            out["appid"] = txt
    elif stype == F.SRC_HTTP_JSON:
        if sflags != 0:
            raise _err("BAD_SOURCE", f"sources[{i}] http_json: flags {sflags} must be 0")
        if s1 == F.NO_STR:
            raise _err("BAD_SOURCE", f"sources[{i}] http_json: url required")
        url = _pool_str(pool, s1)
        _check_string_bytes(url, allow_tokens=False, off=s1)
        urls = url.decode("ascii")
        if not urls.startswith("https://"):
            raise _err("BAD_SOURCE", f"sources[{i}] http_json: url not https")
        if not (F.HTTP_URL_MIN <= len(urls) <= F.HTTP_URL_MAX):
            raise _err("BAD_SOURCE", f"sources[{i}] http_json: url length {len(urls)}")
        out["url"] = urls
        ptr = ""
        if s2 != F.NO_STR:
            ptrb = _pool_str(pool, s2)
            _check_string_bytes(ptrb, allow_tokens=False, off=s2)
            ptr = ptrb.decode("ascii")
            if len(ptr) > F.HTTP_PTR_MAX:
                raise _err("BAD_SOURCE", f"sources[{i}] http_json: pointer too long")
            if ptr and not ptr.startswith("/"):
                raise _err("BAD_SOURCE", f"sources[{i}] http_json: pointer must start with '/'")
        out["pointer"] = ptr

    # poll_s: 0 = default; else >= MIN_POLL (ignored for clock)
    if stype != F.SRC_CLOCK:
        if poll_s != 0 and poll_s < F.MIN_POLL:
            raise _err("BAD_SOURCE", f"sources[{i}]: poll_s {poll_s} < {F.MIN_POLL}")
        out["poll_s"] = poll_s
    else:
        if poll_s != 0:
            raise _err("BAD_SOURCE", f"sources[{i}] clock: poll_s must be 0")
    out["_type_id"] = stype
    return out


def _resolve_tokens_refs(raw: bytes, off: int, sources: list, wid_i: int) -> None:
    """Resolve each token's src/field/idx against the actual source list (BAD_REF)."""
    i = 0
    n = len(raw)
    while i < n:
        if raw[i] != F.TOK:
            i += 1
            continue
        src_i = raw[i + 1] - 1
        field_id = raw[i + 2] - 1
        idx = raw[i + 3] - 1
        spec = raw[i + 4]
        i += 5
        if src_i >= len(sources):
            raise _err("BAD_REF", f"widgets[{wid_i}] token: src {src_i} out of range")
        stype = sources[src_i]["_type_id"]
        fi = F.find_field(stype, field_id)
        if fi is None:
            raise _err("BAD_REF", f"widgets[{wid_i}] token: field {field_id} not in source")
        if fi.kind == "T":
            raise _err("BAD_REF", f"widgets[{wid_i}] token: time field only valid for clock")
        if fi.count == 0:
            if idx != 0:
                raise _err("BAD_REF", f"widgets[{wid_i}] token: idx {idx} on non-indexed field")
        elif idx >= fi.count:
            raise _err("BAD_REF", f"widgets[{wid_i}] token: idx {idx} >= {fi.count}")
        if fi.kind in ("S",) and spec != ord("D"):
            raise _err("BAD_STRING", f"widgets[{wid_i}] token: string field needs 'D' spec")


def _decode_widget(buf, o, pool, render_mode, pool_len, sources, i):
    (wtype, wflags, x, y, w, h, font, align, color, border,
     text_off, aux_off, src, field, idx, p0, p1, max_lines, lo, hi) = struct.unpack_from(
        "<BBHHHHBBBBHHBBBBBBhh", buf, o)

    if wtype not in (F.W_TEXT, F.W_NUMBER, F.W_BAR, F.W_ICON, F.W_IMAGE, F.W_CLOCK):
        raise _err("BAD_WIDGET", f"widgets[{i}]: unknown type {wtype}")
    if wflags & ~(F.WF_INVERT | F.WF_CLIP | F.WF_WRAP | F.WF_IMG_2BPP):
        raise _err("BAD_WIDGET", f"widgets[{i}]: flags 0x{wflags:02x}")
    if w < 1 or h < 1:
        raise _err("BAD_WIDGET", f"widgets[{i}]: w/h must be >= 1")
    if x + w > F.SCREEN_W or y + h > F.SCREEN_H:
        raise _err("BAD_WIDGET", f"widgets[{i}]: box past {F.SCREEN_W}x{F.SCREEN_H}")
    if (align & 0b11) > 2 or ((align >> 2) & 0b11) > 2 or align & ~0b1111:
        raise _err("BAD_WIDGET", f"widgets[{i}]: align 0x{align:02x}")
    if color > 3:
        raise _err("BAD_WIDGET", f"widgets[{i}]: color {color}")
    if border > 4:
        raise _err("BAD_WIDGET", f"widgets[{i}]: border {border}")
    # gray colour on text/number/clock in mono is rejected
    if render_mode == F.RM_MONO and wtype in (F.W_TEXT, F.W_NUMBER, F.W_CLOCK) and color in (1, 2):
        raise _err("BAD_WIDGET", f"widgets[{i}]: gray color {color} on text in mono")

    halign = align & 0b11
    valign = (align >> 2) & 0b11
    out = {
        "type": {F.W_TEXT: "text", F.W_NUMBER: "number", F.W_BAR: "bar",
                 F.W_ICON: "icon", F.W_IMAGE: "image", F.W_CLOCK: "clock"}[wtype],
        "x": x, "y": y, "w": w, "h": h,
        "align": ["left", "center", "right"][halign],
        "valign": ["top", "middle", "bottom"][valign],
        "color": color, "border": border,
        "invert": bool(wflags & F.WF_INVERT),
    }

    def need_src_field(kinds, indexed_ok):
        if src == F.NO_SRC:
            raise _err("BAD_REF", f"widgets[{i}]: source required")
        if src >= len(sources):
            raise _err("BAD_REF", f"widgets[{i}]: src {src} out of range")
        stype = sources[src]["_type_id"]
        fi = F.find_field(stype, field)
        if fi is None:
            raise _err("BAD_REF", f"widgets[{i}]: field {field} not in source")
        if fi.kind not in kinds:
            raise _err("BAD_REF", f"widgets[{i}]: field kind {fi.kind} not in {kinds}")
        if fi.count == 0:
            if idx != 0:
                raise _err("BAD_REF", f"widgets[{i}]: idx {idx} on non-indexed field")
        elif idx >= fi.count:
            raise _err("BAD_REF", f"widgets[{i}]: idx {idx} >= {fi.count}")
        return fi

    # --- per type required/forbidden -------------------------------------
    if wtype == F.W_TEXT:
        if wflags & F.WF_IMG_2BPP:
            raise _err("BAD_WIDGET", f"widgets[{i}] text: img flag set")
        if text_off == F.NO_STR:
            raise _err("BAD_WIDGET", f"widgets[{i}] text: template required")
        if not F.font_valid(font):
            raise _err("BAD_WIDGET", f"widgets[{i}] text: font {font}")
        if src != F.NO_SRC or field != 0 or idx != 0 or p0 != 0 or p1 != 0 or lo != 0 or hi != 0:
            raise _err("BAD_WIDGET", f"widgets[{i}] text: unused fields must be 0")
        if aux_off != F.NO_STR:
            raise _err("BAD_WIDGET", f"widgets[{i}] text: aux must be absent")
        raw = _pool_str(pool, text_off)
        _check_string_bytes(raw, allow_tokens=True, off=text_off)
        _check_template_tokens(raw, text_off)
        if not _font_charset_ok(font, raw):
            raise _err("BAD_STRING", f"widgets[{i}] text: char not in font {font}")
        _resolve_tokens_refs(raw, text_off, sources, i)
        out["text"] = raw
        out["font"] = font
        out["wrap"] = bool(wflags & F.WF_WRAP)
        out["ellipsis"] = not (wflags & F.WF_CLIP)
        out["max_lines"] = max_lines

    elif wtype == F.W_NUMBER:
        if wflags & (F.WF_CLIP | F.WF_WRAP | F.WF_IMG_2BPP):
            raise _err("BAD_WIDGET", f"widgets[{i}] number: text/img flags set")
        if not F.font_valid(font):
            raise _err("BAD_WIDGET", f"widgets[{i}] number: font {font}")
        if text_off != F.NO_STR:
            raise _err("BAD_WIDGET", f"widgets[{i}] number: text must be absent")
        if lo != 0 or hi != 0 or p1 != 0:
            raise _err("BAD_WIDGET", f"widgets[{i}] number: unused fields must be 0")
        if not (p0 <= 6 or p0 == F.DECIMALS_DEFAULT):
            raise _err("BAD_WIDGET", f"widgets[{i}] number: decimals {p0}")
        fi = need_src_field(("N", "A"), False)
        out["bind"] = f"{_srcname(sources, src)}.{_pathidx(fi, idx)}"
        out["decimals"] = p0
        out["font"] = font
        if aux_off != F.NO_STR:
            unit = _pool_str(pool, aux_off)
            _check_string_bytes(unit, allow_tokens=False, off=aux_off)
            if not _font_charset_ok(font, unit):
                raise _err("BAD_STRING", f"widgets[{i}] number: unit char not in font")
            out["unit"] = unit.decode("latin-1")
        else:
            out["unit"] = ""

    elif wtype == F.W_BAR:
        if wflags & (F.WF_CLIP | F.WF_WRAP | F.WF_IMG_2BPP):
            raise _err("BAD_WIDGET", f"widgets[{i}] bar: text/img flags set")
        if text_off != F.NO_STR or aux_off != F.NO_STR:
            raise _err("BAD_WIDGET", f"widgets[{i}] bar: text/aux must be absent")
        if font != 0 or p1 != 0:
            raise _err("BAD_WIDGET", f"widgets[{i}] bar: unused fields must be 0")
        if p0 not in (0, 1):
            raise _err("BAD_WIDGET", f"widgets[{i}] bar: orientation {p0}")
        if lo >= hi:
            raise _err("BAD_WIDGET", f"widgets[{i}] bar: lo {lo} >= hi {hi}")
        fi = need_src_field(("N", "A"), False)
        out["bind"] = f"{_srcname(sources, src)}.{_pathidx(fi, idx)}"
        out["range"] = [lo, hi]
        out["orientation"] = "vertical" if p0 == 1 else "horizontal"

    elif wtype == F.W_ICON:
        if wflags & (F.WF_CLIP | F.WF_WRAP | F.WF_IMG_2BPP):
            raise _err("BAD_WIDGET", f"widgets[{i}] icon: text/img flags set")
        if text_off != F.NO_STR or aux_off != F.NO_STR or font != 0:
            raise _err("BAD_WIDGET", f"widgets[{i}] icon: text/aux/font must be absent")
        if p0 == F.ICONS_WEATHER:
            if p1 != 0:
                raise _err("BAD_WIDGET", f"widgets[{i}] icon: weather set p1 must be 0")
            fi = need_src_field(("N",), False)
            out["bind"] = f"{_srcname(sources, src)}.{_pathidx(fi, idx)}"
            out["set"] = "weather"
        elif p0 == F.ICONS_NAMED:
            if p1 >= len(F.ICON_NAMES):
                raise _err("BAD_WIDGET", f"widgets[{i}] icon: id {p1} >= {len(F.ICON_NAMES)}")
            if src != F.NO_SRC or field != 0 or idx != 0:
                raise _err("BAD_WIDGET", f"widgets[{i}] icon: named set src must be absent")
            out["name"] = F.ICON_NAMES[p1]
        else:
            raise _err("BAD_WIDGET", f"widgets[{i}] icon: set {p0}")

    elif wtype == F.W_IMAGE:
        if wflags & (F.WF_CLIP | F.WF_WRAP):
            raise _err("BAD_WIDGET", f"widgets[{i}] image: text flags set")
        two_bpp = bool(wflags & F.WF_IMG_2BPP)
        if two_bpp and render_mode != F.RM_GRAY4:
            raise _err("BAD_WIDGET", f"widgets[{i}] image: 2bpp in mono layout")
        if text_off != F.NO_STR or font != 0 or src != F.NO_SRC or field != 0 or idx != 0:
            raise _err("BAD_WIDGET", f"widgets[{i}] image: text/font/src must be absent")
        if p0 != 0 or p1 != 0 or lo != 0 or hi != 0:
            raise _err("BAD_WIDGET", f"widgets[{i}] image: unused fields must be 0")
        if aux_off == F.NO_STR:
            raise _err("BAD_WIDGET", f"widgets[{i}] image: bitmap required")
        stride = (w + 3) // 4 if two_bpp else (w + 7) // 8
        need = stride * h
        if aux_off + need > pool_len:
            raise _err("BAD_WIDGET", f"widgets[{i}] image: bitmap runs past pool")
        out["_bitmap"] = pool[aux_off : aux_off + need]
        out["_2bpp"] = two_bpp

    elif wtype == F.W_CLOCK:
        if wflags & (F.WF_CLIP | F.WF_WRAP | F.WF_IMG_2BPP):
            raise _err("BAD_WIDGET", f"widgets[{i}] clock: text/img flags set")
        if not F.font_valid(font):
            raise _err("BAD_WIDGET", f"widgets[{i}] clock: font {font}")
        if text_off == F.NO_STR:
            raise _err("BAD_WIDGET", f"widgets[{i}] clock: format required")
        if aux_off != F.NO_STR or p0 != 0 or p1 != 0 or lo != 0 or hi != 0:
            raise _err("BAD_WIDGET", f"widgets[{i}] clock: unused fields must be 0")
        fi = need_src_field(("T",), False)
        raw = _pool_str(pool, text_off)
        _check_string_bytes(raw, allow_tokens=False, off=text_off)
        _check_clock_format(raw, text_off)
        if not _clock_font_charset_ok(font, raw):
            raise _err("BAD_STRING", f"widgets[{i}] clock: literal char not in font {font}")
        out["format"] = raw.decode("ascii")
        out["font"] = font
        out["bind"] = f"{_srcname(sources, src)}.{_pathidx(fi, idx)}"

    return out


def _srcname(sources, idx):
    return f"src{idx}"  # names are not on the wire; decode exposes positional ids


def _pathidx(fi, idx):
    return f"{fi.path}[{idx}]" if fi.count else fi.path
