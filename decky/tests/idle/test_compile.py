"""Every widget type compiles and decodes back to the expected fields; determinism;
pool dedupe; size cap boundary; header/structure invariants."""
import struct
import zlib

import pytest

from inkterface_core.idle import fields as F
from inkterface_core.idle.compile import compile_layout, LayoutError
from inkterface_core.idle.decode import decode_layout
from helpers import CLOCK_SRC, WEATHER_SRC, STEAM_SRC, HTTP_SRC, compile_ok


def _one(widget, **kw):
    b = compile_ok([widget], **kw)
    return decode_layout(b)["widgets"][0], b


def test_header_and_magic():
    b = compile_ok([{"type": "text", "x": 0, "y": 0, "w": 10, "h": 10, "text": "hi"}])
    assert b[0:4] == b"ILY1"
    assert b[4] == 1  # version
    assert b[5] == 0  # mono
    (magic, ver, mode, refresh, full, flags, ns, nw, res, plen) = struct.unpack_from(
        "<4sBBHHBBBBH", b, 0)
    assert refresh == 60 and full == 60 and flags == 0 and ns == 0 and nw == 1 and res == 0
    # crc covers everything before it
    assert struct.unpack_from("<I", b, len(b) - 4)[0] == zlib.crc32(b[:-4]) & 0xFFFFFFFF


def test_gray4_default_refresh():
    b = compile_ok([{"type": "text", "x": 0, "y": 0, "w": 10, "h": 10, "text": "x"}], mode="gray4")
    assert struct.unpack_from("<H", b, 6)[0] == 900  # gray4 default refresh


def test_dark_flag():
    b = compile_ok([{"type": "text", "x": 0, "y": 0, "w": 10, "h": 10, "text": "x"}], dark=True)
    assert decode_layout(b)["dark"] is True


def test_text_widget():
    w, _ = _one({"type": "text", "x": 1, "y": 2, "w": 100, "h": 24, "text": "Hello",
                 "font": "regular-18", "align": "center", "valign": "middle",
                 "wrap": True, "max_lines": 3})
    assert w["type"] == "text"
    assert (w["x"], w["y"], w["w"], w["h"]) == (1, 2, 100, 24)
    assert w["align"] == "center" and w["valign"] == "middle"
    assert w["wrap"] is True and w["max_lines"] == 3 and w["ellipsis"] is True
    assert w["text"] == b"Hello"


def test_text_ellipsis_false_sets_clip():
    w, _ = _one({"type": "text", "x": 0, "y": 0, "w": 100, "h": 24, "text": "x",
                 "ellipsis": False})
    assert w["ellipsis"] is False


def test_text_template_token_and_escape():
    w, _ = _one({"type": "text", "x": 0, "y": 0, "w": 200, "h": 24,
                 "text": "T {wx.current.temp:.1f} {{x}}", "font": "regular-18"},
                sources=WEATHER_SRC)
    raw = w["text"]
    assert raw.startswith(b"T ")
    assert b"{x}" in raw            # {{ }} collapsed to literals
    assert bytes([F.TOK]) in raw    # a 5-byte token present
    # token: TOK, src+1, field+1(temp=0->1), idx+1, spec '1'
    ti = raw.index(F.TOK)
    assert raw[ti + 1] == 1 and raw[ti + 2] == 1 and raw[ti + 3] == 1 and raw[ti + 4] == ord("1")


def test_degree_sign_becomes_0x7f():
    w, _ = _one({"type": "text", "x": 0, "y": 0, "w": 100, "h": 24, "text": "25\u00b0",
                 "font": "regular-18"})
    assert w["text"] == b"25\x7f"


def test_number_widget_formats():
    for fmt, dec in [("{:.0f}", 0), ("{:.2f}", 2), ("{:d}", 0), ("{}", F.DECIMALS_DEFAULT), (None, F.DECIMALS_DEFAULT)]:
        wd = {"type": "number", "x": 0, "y": 0, "w": 100, "h": 40,
              "bind": "wx.current.temp", "unit": " C", "font": "bold-28"}
        if fmt is not None:
            wd["format"] = fmt
        w, _ = _one(wd, sources=WEATHER_SRC)
        assert w["type"] == "number" and w["decimals"] == dec
        assert w["unit"] == " C" and w["bind"].endswith("current.temp")


def test_bar_widget():
    w, _ = _one({"type": "bar", "x": 0, "y": 0, "w": 200, "h": 20,
                 "bind": "wx.today.rain", "range": [0, 100], "orientation": "vertical"},
                sources=WEATHER_SRC)
    assert w["type"] == "bar" and w["range"] == [0, 100] and w["orientation"] == "vertical"


def test_icon_weather_and_named():
    w, _ = _one({"type": "icon", "x": 0, "y": 0, "w": 48, "h": 48,
                 "bind": "wx.current.code", "set": "weather"}, sources=WEATHER_SRC)
    assert w["set"] == "weather" and w["bind"].endswith("current.code")
    w2, _ = _one({"type": "icon", "x": 0, "y": 0, "w": 48, "h": 48, "name": "gamepad"})
    assert w2["name"] == "gamepad"


def test_clock_widget_default_bind():
    w, _ = _one({"type": "clock", "x": 0, "y": 0, "w": 600, "h": 180,
                 "format": "%H:%M", "font": "bold-160"}, sources=CLOCK_SRC)
    assert w["type"] == "clock" and w["format"] == "%H:%M"


def test_clock_indexed_forecast_binding_resolves():
    w, _ = _one({"type": "text", "x": 0, "y": 0, "w": 200, "h": 24,
                 "text": "{wx.forecast[2].day}", "font": "regular-18"},
                sources=WEATHER_SRC)
    ti = w["text"].index(F.TOK)
    # weather forecast.day = field id 8, idx 2 -> stored +1
    assert w["text"][ti + 2] == 9 and w["text"][ti + 3] == 3


def test_image_widget_mono_and_gray4(tmp_path):
    from PIL import Image
    assets = tmp_path
    Image.new("L", (24, 8), 100).save(assets / "pic.png")
    for mode, stride, flag in [("mono", 3, False), ("gray4", 6, True)]:
        w, _ = _one({"type": "image", "x": 0, "y": 0, "w": 24, "h": 8, "src": "pic.png"},
                    mode=mode, assets_dir=str(assets))
        assert w["_2bpp"] is flag and len(w["_bitmap"]) == stride * 8


def test_steam_and_http_sources_round_trip():
    b = compile_ok([{"type": "number", "x": 0, "y": 0, "w": 80, "h": 30,
                     "bind": "st.players"}], sources=STEAM_SRC)
    assert decode_layout(b)["sources"][0]["type"] == "steam"
    b2 = compile_ok([{"type": "number", "x": 0, "y": 0, "w": 80, "h": 30,
                      "bind": "api.value"}], sources=HTTP_SRC)
    s = decode_layout(b2)["sources"][0]
    assert s["type"] == "http_json" and s["url"].startswith("https://") and s["pointer"] == "/a/b"


def test_determinism():
    widgets = [{"type": "text", "x": 0, "y": 0, "w": 100, "h": 24, "text": "a {wx.current.temp:.0f}",
                "font": "regular-18"},
               {"type": "number", "x": 0, "y": 30, "w": 80, "h": 30, "bind": "wx.current.temp"}]
    a = compile_ok(widgets, sources=WEATHER_SRC)
    b = compile_ok(widgets, sources=WEATHER_SRC)
    assert a == b


def test_pool_dedupe():
    # two identical text strings share one pool entry; pool[0] is the empty string
    w = {"type": "text", "x": 0, "y": 0, "w": 100, "h": 24, "text": "SAME", "font": "regular-18"}
    w2 = dict(w, y=30)
    b = compile_ok([w, w2])
    (plen,) = struct.unpack_from("<H", b, 14)
    # pool = NUL + "SAME\0" = 6 bytes, not 11
    assert plen == 6


def test_size_cap_boundary():
    # 8142 chars -> exactly 8192 bytes (ok); 8143 -> 8193 (refused).
    ok = compile_ok([{"type": "text", "x": 0, "y": 0, "w": 640, "h": 20, "text": "a" * 8142}])
    assert len(ok) == F.MAX_LAYOUT_BYTES
    with pytest.raises(LayoutError, match="> 8192"):
        compile_ok([{"type": "text", "x": 0, "y": 0, "w": 640, "h": 20, "text": "a" * 8143}])


def test_exact_8192_ok_8193_refused():
    # directly serialize-test the decoder boundary: a layout of exactly MAX is accepted,
    # one byte larger is TOO_BIG.
    b = compile_ok([{"type": "text", "x": 0, "y": 0, "w": 640, "h": 20, "text": "hi"}])
    assert len(b) <= F.MAX_LAYOUT_BYTES
    assert decode_layout(b)  # ok
    from inkterface_core.idle.decode import DecodeError
    with pytest.raises(DecodeError) as e:
        decode_layout(b"ILY1" + bytes(F.MAX_LAYOUT_BYTES))  # > 8192
    assert e.value.code == "TOO_BIG"
