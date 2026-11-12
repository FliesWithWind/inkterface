"""Every compile-time rejection in the brief, asserting the message names the json path."""
import pytest

from inkterface_core.idle.compile import compile_layout, LayoutError
from helpers import CLOCK_SRC, WEATHER_SRC, STEAM_SRC, layout


def err(widgets, *, mode="mono", sources=None, **kw):
    with pytest.raises(LayoutError) as e:
        compile_layout(layout(widgets, mode=mode, sources=sources, **kw))
    return "\n".join(e.value.problems)


def test_unknown_top_key():
    assert "idle: unknown keys" in err([{"type": "text", "x": 0, "y": 0, "w": 5, "h": 5, "text": "a"}],
                                       foo=1)


def test_unknown_widget_key():
    msg = err([{"type": "text", "x": 0, "y": 0, "w": 5, "h": 5, "text": "a", "zzz": 1}])
    assert "idle.widgets[0]: unknown keys" in msg and "zzz" in msg


def test_unknown_widget_type():
    assert "idle.widgets[0].type" in err([{"type": "blob", "x": 0, "y": 0, "w": 5, "h": 5}])


def test_unknown_source_type():
    assert "idle.sources.s.type" in err([{"type": "text", "x": 0, "y": 0, "w": 5, "h": 5, "text": "a"}],
                                        sources={"s": {"type": "nope"}})


def test_unknown_field_in_bind():
    assert "idle.widgets[0].bind" in err(
        [{"type": "number", "x": 0, "y": 0, "w": 5, "h": 5, "bind": "wx.nope"}], sources=WEATHER_SRC)


def test_unknown_font():
    assert "idle.widgets[0].font" in err(
        [{"type": "text", "x": 0, "y": 0, "w": 5, "h": 5, "text": "a", "font": "comic-12"}])


def test_bad_geometry_past_edge():
    msg = err([{"type": "text", "x": 600, "y": 0, "w": 100, "h": 5, "text": "a"}])
    assert "idle.widgets[0]" in msg and "648px edge" in msg


def test_zero_size():
    assert "w and h must be >= 1" in err([{"type": "text", "x": 0, "y": 0, "w": 0, "h": 5, "text": "a"}])


def test_index_out_of_range():
    assert "index 3 out of range" in err(
        [{"type": "text", "x": 0, "y": 0, "w": 100, "h": 20, "text": "{wx.forecast[3].day}"}],
        sources=WEATHER_SRC)


def test_wrong_field_kind_for_widget():
    # weather condition is a string; a number widget wants N/A
    assert "idle.widgets[0].bind" in err(
        [{"type": "number", "x": 0, "y": 0, "w": 50, "h": 20, "bind": "wx.current.condition"}],
        sources=WEATHER_SRC)


def test_time_field_in_template():
    assert "time field" in err(
        [{"type": "text", "x": 0, "y": 0, "w": 100, "h": 20, "text": "{time.now}"}],
        sources=CLOCK_SRC)


def test_gray_text_in_mono():
    msg = err([{"type": "text", "x": 0, "y": 0, "w": 50, "h": 20, "text": "a", "color": 1}])
    assert "idle.widgets[0].color" in msg and "mono" in msg


def test_2bpp_image_in_mono(tmp_path):
    from PIL import Image
    Image.new("L", (8, 8), 0).save(tmp_path / "p.png")
    # image compiles as 1bpp in mono; a gray4-only 2bpp can't be requested in mono
    # (the flag is derived from mode, so mono never produces a 2bpp; assert decode guards it)
    from inkterface_core.idle.decode import decode_layout, DecodeError
    from inkterface_core.idle.compile import compile_layout as cl
    b = cl(layout([{"type": "image", "x": 0, "y": 0, "w": 8, "h": 8, "src": "p.png"}],
                  mode="gray4"), assets_dir=str(tmp_path))
    # flip mode byte to mono, fix crc -> decoder must reject the 2bpp flag
    import zlib, struct
    ba = bytearray(b)
    ba[5] = 0  # render_mode mono
    struct.pack_into("<I", ba, len(ba) - 4, zlib.crc32(bytes(ba[:-4])) & 0xFFFFFFFF)
    with pytest.raises(DecodeError) as e:
        decode_layout(bytes(ba))
    assert e.value.code == "BAD_WIDGET" and "2bpp in mono" in e.value.msg


def test_too_many_widgets():
    ws = [{"type": "text", "x": 0, "y": 0, "w": 5, "h": 5, "text": "a"} for _ in range(65)]
    assert "> 64" in err(ws)


def test_too_many_sources():
    srcs = {f"c{i}": {"type": "clock"} for i in range(9)}
    assert "> 8" in err([{"type": "text", "x": 0, "y": 0, "w": 5, "h": 5, "text": "a"}], sources=srcs)


def test_http_url_not_https():
    assert "must be https" in err(
        [{"type": "number", "x": 0, "y": 0, "w": 5, "h": 5, "bind": "a.value"}],
        sources={"a": {"type": "http_json", "url": "http://x.example.com/aaaa"}})


def test_http_url_too_long():
    long = "https://e.example.com/" + "a" * 300
    assert "out of" in err(
        [{"type": "number", "x": 0, "y": 0, "w": 5, "h": 5, "bind": "a.value"}],
        sources={"a": {"type": "http_json", "url": long}})


def test_poll_too_small():
    assert "poll_s" in err(
        [{"type": "number", "x": 0, "y": 0, "w": 5, "h": 5, "bind": "wx.current.temp"}],
        sources={"wx": {"type": "weather", "poll_s": 10}})


def test_char_font_lacks():
    # digit font cannot render letters
    assert "not in font" in err(
        [{"type": "number", "x": 0, "y": 0, "w": 50, "h": 20, "bind": "wx.current.temp",
          "unit": "deg", "font": "bold-160"}], sources=WEATHER_SRC)


def test_non_ascii_rejected():
    msg = err([{"type": "text", "x": 0, "y": 0, "w": 50, "h": 20, "text": "caf\u00e9"}])
    assert "idle.widgets[0].text" in msg and "not allowed" in msg


def test_bar_range_bad():
    assert "lo" in err([{"type": "bar", "x": 0, "y": 0, "w": 50, "h": 20,
                         "bind": "wx.today.rain", "range": [100, 0]}], sources=WEATHER_SRC)


def test_every_problem_listed():
    msg = err([{"type": "text", "x": 700, "y": 0, "w": 100, "h": 5, "text": "a", "color": 1}])
    # both the geometry and the gray-in-mono problems appear
    assert "648px edge" in msg and "mono" in msg
