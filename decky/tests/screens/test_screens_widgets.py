import math

import pytest
from PIL import Image
from screens_testlib import ink, png_bytes

from inkterface_core.screens import widgets as WG
from inkterface_core.screens.data import DataSnapshot
from inkterface_core.screens.fonts import MAX_SIZE, MIN_SIZE, get_font, parse_font
from inkterface_core.screens.icons import ICONS, draw_icon


def draw(type_, w=200, h=60, data=None, assets_dir=None, **opts):
    widget = {"type": type_, "x": 0, "y": 0, "w": w, "h": h, **opts}
    return WG.render_widget(widget, DataSnapshot(data or {}), assets_dir)


def same(a, b):
    return a.tobytes() == b.tobytes()


# ---- fonts / helpers --------------------------------------------------------------------

def test_font_parsing():
    assert parse_font("bold-32") == ("bold", 32)
    for bad in ("bold", "italic-12", f"bold-{MIN_SIZE - 1}", f"bold-{MAX_SIZE + 1}", "bold-1000", None, 12):
        with pytest.raises(ValueError):
            parse_font(bad)
    assert get_font("regular-16") is get_font("regular-16")


def test_fmt_number():
    assert WG.fmt_number(3.14159, None, 2) == "3.14"
    assert WG.fmt_number(3.14159, "T={:.1f}C", 0) == "T=3.1C"
    assert WG.fmt_number(5, "{}", 0) == "5"
    assert WG.fmt_number(-0.4, None, 0) == "0"
    assert WG.fmt_number(-0.001, None, 2) == "0.00"
    assert WG.fmt_number(-1.5, None, 0) == "-2"
    assert WG.fmt_number(1.5, "{:d}", 0) is None        # spec does not fit the value
    assert WG.fmt_number(1.5, "{0.real}", 0) is None    # attribute access refused
    assert WG.fmt_number(10 ** 400 if False else 1e308, None, 0).startswith("1")


def test_fmt_duration():
    assert WG.fmt_duration(0) == "0s"
    assert WG.fmt_duration(45) == "45s"
    assert WG.fmt_duration(60) == "1m"
    assert WG.fmt_duration(83 * 60) == "1h 23m"
    assert WG.fmt_duration(8525 * 60) == "142h 05m"
    assert WG.fmt_duration(-5) == "0s"


def test_wrap_and_ellipsize():
    f = get_font("regular-16")
    assert WG.wrap_lines("", f, 100) == [""]
    assert WG.wrap_lines("a\nb", f, 100) == ["a", "b"]
    lines = WG.wrap_lines("hello world foo", f, f.getlength("hello world"))
    assert lines == ["hello world", "foo"]
    broken = WG.wrap_lines("x" * 40, f, 50)
    assert len(broken) > 1 and "".join(broken) == "x" * 40 and all(f.getlength(b) <= 50 for b in broken)
    assert WG.wrap_lines("abc", f, 1) == ["a", "b", "c"]      # narrower than a glyph still terminates
    assert WG.ellipsize("short", f, 500) == "short…"
    out = WG.ellipsize("a very long sentence that cannot fit", f, 80)
    assert out.endswith("…") and f.getlength(out) <= 80
    assert WG.ellipsize("anything", f, 2) == ""


# ---- common: invert, border, determinism, geometry ----------------------------------------

def test_tile_size_and_determinism():
    a = draw("text", 123, 45, text="Hello")
    assert a.size == (123, 45) and a.mode == "1"
    assert same(a, draw("text", 123, 45, text="Hello"))


def test_invert_swaps_colours():
    t = draw("text", 80, 30, text="Hi", invert=True)
    assert t.getpixel((0, 0)) == 0 and t.getpixel((79, 29)) == 0
    assert ink(t) < t.width * t.height      # white glyph pixels
    plain = draw("text", 80, 30, text="Hi")
    assert plain.getpixel((0, 0)) == 255


def test_border_draws_frame():
    t = draw("text", 50, 20, text="", border=2)
    for x, y in ((0, 0), (49, 0), (0, 19), (1, 1), (25, 1), (25, 18)):
        assert t.getpixel((x, y)) == 0
    assert t.getpixel((25, 10)) == 255


# ---- text ----------------------------------------------------------------------------------

def test_text_alignment_by_region():
    left = draw("text", 300, 30, text="Hi", align="left")
    right = draw("text", 300, 30, text="Hi", align="right")
    mid = draw("text", 300, 30, text="Hi", align="center")
    assert ink(left, (150, 0, 300, 30)) == 0 and ink(left, (0, 0, 40, 30)) > 0
    assert ink(right, (0, 0, 150, 30)) == 0 and ink(right, (260, 0, 300, 30)) > 0
    assert ink(mid, (0, 0, 100, 30)) == 0 and ink(mid, (200, 0, 300, 30)) == 0 and ink(mid, (120, 0, 180, 30)) > 0


def test_text_valign():
    top = draw("text", 100, 100, text="Hi", valign="top")
    bot = draw("text", 100, 100, text="Hi", valign="bottom")
    mid = draw("text", 100, 100, text="Hi", valign="middle")
    assert ink(top, (0, 50, 100, 100)) == 0
    assert ink(bot, (0, 0, 100, 50)) == 0
    assert ink(mid, (0, 0, 100, 25)) == 0 and ink(mid, (0, 75, 100, 100)) == 0 and ink(mid, (0, 25, 100, 75)) > 0


def test_text_template_and_missing():
    t = draw("text", text="CPU {system.cpu.load:.0f}%", data={"system.cpu.load": 42.4})
    assert same(t, draw("text", text="CPU 42%"))
    miss = draw("text", text="CPU {system.cpu.load:.0f}%")
    assert same(miss, draw("text", text="CPU --%"))


def test_text_wrap_and_max_lines():
    long = "alpha beta gamma delta epsilon zeta eta theta"
    one = draw("text", 120, 200, text=long)
    wrapped = draw("text", 120, 200, text=long, wrap=True)
    assert ink(one, (0, 30, 120, 200)) == 0 and ink(wrapped, (0, 30, 120, 200)) > 0
    capped = draw("text", 120, 200, text=long, wrap=True, max_lines=2)
    assert ink(capped, (0, 50, 120, 200)) == 0 and ink(capped, (0, 0, 120, 50)) > 0


def test_text_wrap_truncates_with_ellipsis_to_box_height():
    long = "word " * 40
    with_e = draw("text", 120, 40, text=long, wrap=True)
    no_e = draw("text", 120, 40, text=long, wrap=True, ellipsis=False)
    assert not same(with_e, no_e) and ink(with_e) > 0


def test_text_long_single_line_ellipsis_vs_clip():
    long = "x" * 100
    e = draw("text", 100, 24, text=long)
    c = draw("text", 100, 24, text=long, ellipsis=False)
    assert not same(e, c)
    assert ink(c, (90, 0, 100, 24)) > 0          # clipped text reaches the edge
    assert ink(e, (99, 0, 100, 24)) == 0         # ellipsis ends inside the box


def test_text_newline_flattened_when_not_wrapping():
    assert same(draw("text", text="a\nb"), draw("text", text="a b"))


def test_text_empty_and_tiny_box():
    assert ink(draw("text", 50, 20, text="")) == 0
    assert draw("text", 1, 1, text="Hello").size == (1, 1)
    assert draw("text", 5, 3, text="Hello", wrap=True).size == (5, 3)


def test_text_unicode():
    assert ink(draw("text", text="°C … é")) > 0


# ---- number ----------------------------------------------------------------------------------

def test_number_basic_precision_format_unit():
    d = {"v": 3.14159}
    assert same(draw("number", bind="system.cpu.load", precision=2, data={"system.cpu.load": 3.14159}),
                draw("number", bind="system.cpu.load", format="{:.2f}", data={"system.cpu.load": 3.14159}))
    with_unit = draw("number", bind="system.cpu.load", unit="%", data={"system.cpu.load": 5})
    without = draw("number", bind="system.cpu.load", data={"system.cpu.load": 5})
    assert ink(with_unit) > ink(without) and not same(with_unit, without) and d


def test_number_align():
    data = {"system.cpu.load": 5}
    left = draw("number", 300, 40, bind="system.cpu.load", align="left", data=data)
    right = draw("number", 300, 40, bind="system.cpu.load", align="right", data=data)
    assert ink(left, (150, 0, 300, 40)) == 0 and ink(right, (0, 0, 150, 40)) == 0 and ink(right) > 0


def test_number_missing_and_invalid_show_placeholder():
    ph = draw("number", bind="system.cpu.load", data={"system.cpu.load": None})
    assert ink(ph) > 0
    for bad in (None, "x", True, math.nan, math.inf, [1]):
        assert same(ph, draw("number", bind="system.cpu.load", data={"system.cpu.load": bad}))
    assert same(ph, draw("number", bind="system.cpu.load"))
    # a format that cannot render the value also falls back
    assert same(ph, draw("number", bind="system.cpu.load", format="{:d}", data={"system.cpu.load": 1.5}))
    assert ink(draw("number", bind="system.cpu.load", unit="%")) == ink(ph)   # no unit next to a placeholder


def test_number_negative_zero_equals_zero():
    assert same(draw("number", bind="system.cpu.load", data={"system.cpu.load": -0.4}),
                draw("number", bind="system.cpu.load", data={"system.cpu.load": 0}))


@pytest.mark.parametrize("value", [1e308, -1e308, 123456789012345678, 0, -5])
def test_number_extremes_stay_inside_box(value):
    t = draw("number", 120, 40, bind="system.cpu.load", data={"system.cpu.load": value}, font="bold-32", unit="MHz")
    assert t.size == (120, 40) and ink(t) > 0


def test_number_shrinks_to_fit():
    big = 12345678
    t = draw("number", 100, 40, bind="system.cpu.load", font="bold-40", data={"system.cpu.load": big})
    assert ink(t, (96, 0, 100, 40)) == 0     # right edge untouched: font was reduced until it fit


def test_number_unit_template():
    t = draw("number", bind="system.cpu.load", unit="{settings.u}", data={"system.cpu.load": 1, "settings": {"u": "W"}})
    assert same(t, draw("number", bind="system.cpu.load", unit="W", data={"system.cpu.load": 1}))


# ---- sparkline -------------------------------------------------------------------------------

SP = "system.cpu.load.history"


def spark(series, w=200, h=60, **kw):
    return draw("sparkline", w, h, bind=SP, data={SP: series}, **kw)


def test_sparkline_draws_and_is_deterministic():
    s = [1, 5, 2, 8, 3]
    t = spark(s)
    assert ink(t) > 0 and same(t, spark(s)) and not same(t, spark(s[::-1]))


def test_sparkline_scale_orientation():
    up = spark([0, 100], range=[0, 100], marker=False)
    assert up.getpixel((0, 57)) == 0 and up.getpixel((197, 2)) == 0    # starts bottom-left, ends top-right
    assert up.getpixel((0, 2)) == 255


def test_sparkline_placeholder_when_short_or_missing():
    ph = spark(None)
    assert ink(ph) > 0
    for bad in ([], [1], ["a", None], "x"):
        assert same(ph, spark(bad))
    assert same(ph, draw("sparkline", bind=SP))


def test_sparkline_filters_non_numeric_points():
    assert same(spark([1, 2, "x", None, 3, math.nan]), spark([1, 2, 3]))


def test_sparkline_clamps_to_range():
    assert same(spark([-50, 500], range=[0, 100]), spark([0, 100], range=[0, 100]))


def test_sparkline_flat_series_does_not_divide_by_zero():
    t = spark([5, 5, 5, 5])
    assert ink(t) > 0


def test_sparkline_window_uses_last_points():
    assert same(spark([100, 100, 1, 2, 3], window=3), spark([1, 2, 3]))


def test_sparkline_options_change_output():
    base = spark([1, 5, 2, 8, 3])
    assert not same(base, spark([1, 5, 2, 8, 3], fill=True)) and ink(spark([1, 5, 2, 8, 3], fill=True)) > ink(base)
    assert not same(base, spark([1, 5, 2, 8, 3], thickness=3))
    assert ink(spark([1, 5, 2, 8, 3], marker=False)) < ink(base)
    labelled = spark([1, 5, 2, 8, 3], show_labels=True, range=[0, 100])
    assert ink(labelled, (0, 0, 24, 14)) > 0 and ink(labelled, (0, 46, 24, 60)) > 0       # "100" top-left, "0" bottom-left
    assert ink(spark([1, 5, 2, 8, 3], range=[0, 100]), (0, 0, 24, 14)) == 0
    assert not same(spark([1, 2], show_labels=True, label_format="{:.1f}"), spark([1, 2], show_labels=True))


def test_sparkline_tiny_box_falls_back_to_placeholder():
    assert same(spark([1, 2, 3], 20, 60, show_labels=True, range=[0, 100000]), spark(None, 20, 60))
    assert same(spark([1, 2, 3], 60, 4), spark(None, 60, 4))


def test_sparkline_fill_follows_steep_segments():
    t = spark([0, 100, 0, 100], fill=True, range=[0, 100])
    assert ink(t) > 0


# ---- bar -------------------------------------------------------------------------------------

def bar(v, w=102, h=20, **kw):
    return draw("bar", w, h, bind="system.cpu.load", data={"system.cpu.load": v}, **kw)


def test_bar_fill_levels():
    assert ink(bar(0), (2, 2, 100, 18)) == 0
    half = bar(50)
    assert half.getpixel((10, 10)) == 0 and half.getpixel((90, 10)) == 255
    full = bar(100)
    assert full.getpixel((98, 10)) == 0 and ink(full, (2, 2, 100, 18)) == 98 * 16
    assert ink(bar(25)) < ink(half) < ink(full)


def test_bar_clamps_and_missing():
    assert same(bar(-10), bar(0)) and same(bar(1000), bar(100))
    ph = bar(None)
    assert ink(ph) > 0 and ink(ph, (2, 2, 100, 18)) > 0                    # outline + "--"
    assert same(ph, bar("x"))
    small = bar(None, 20, 10)                                               # too small for text: outline only
    assert ink(small) == 2 * 20 + 2 * 8


def test_bar_vertical_and_range():
    v = draw("bar", 20, 102, bind="system.cpu.load", data={"system.cpu.load": 50}, orientation="vertical")
    assert v.getpixel((10, 95)) == 0 and v.getpixel((10, 10)) == 255
    r = bar(15, range=[10, 20])
    assert same(r, bar(50))
    assert ink(bar(1, 4, 4)) > 0        # degenerate sizes do not crash
    assert ink(bar(0, 3, 3)) > 0


# ---- gauge -----------------------------------------------------------------------------------

def ring(img):
    """The gauge without its centre text."""
    out = img.copy()
    out.paste(255, (40, 40, 120, 120))
    return out


def gauge(v, **kw):
    return draw("gauge", 160, 160, bind="system.cpu.temp", data={"system.cpu.temp": v}, **kw)


def test_gauge_value_arc_grows():
    a, b, c = gauge(0), gauge(50), gauge(100)
    assert ink(ring(a)) < ink(ring(b)) < ink(ring(c))
    assert same(ring(a), ring(gauge(-20))) and same(ring(c), ring(gauge(500)))
    assert same(ring(gauge(10, range=[0, 20])), ring(gauge(50)))


def test_gauge_text_and_missing():
    ph = gauge(None)
    assert ink(ph, (50, 60, 110, 100)) > 0                                   # "--" in the middle
    assert same(ph, gauge("x")) and same(ph, draw("gauge", 160, 160, bind="system.cpu.temp"))
    assert ink(gauge(50, unit="C")) > ink(gauge(50))
    assert not same(gauge(50, format="{:.1f}"), gauge(50))
    assert same(gauge(1.5, format="{:d}"), gauge(1.5, format="{:d}"))       # bad spec: placeholder, no crash


def test_gauge_geometry_options():
    assert not same(gauge(50, thickness=4), gauge(50, thickness=20))
    assert not same(gauge(50, sweep=180, start_angle=180), gauge(50))
    assert gauge(50, thickness=40).size == (160, 160)                        # thickness clamped to the radius
    assert ink(draw("gauge", 30, 30, bind="system.cpu.temp", data={"system.cpu.temp": 50}, font="bold-20")) > 0


def test_gauge_non_square_box_centres_on_short_side():
    t = draw("gauge", 300, 100, bind="system.cpu.temp", data={"system.cpu.temp": 50})
    assert ink(t, (0, 0, 70, 100)) == 0 and ink(t, (230, 0, 300, 100)) == 0 and ink(t, (100, 0, 200, 100)) > 0


# ---- icon ------------------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(ICONS))
def test_every_icon_draws_at_several_sizes(name):
    for size in (8, 24, 48, 96):
        t = draw_icon(name, size)
        assert t.size == (size, size) and 0 < ink(t) < size * size
    assert same(draw_icon(name, 32), draw_icon(name, 32))
    inv = draw_icon(name, 32, ink=1, paper=0)
    assert inv.convert("L").tobytes().count(255) == ink(draw_icon(name, 32))


def test_icon_set_has_a_handful():
    assert {"cpu", "gpu", "thermometer", "fan", "bolt", "gamepad", "sun", "cloud", "rain", "clock"} == set(ICONS)


def test_icon_widget_name_bind_and_fallback():
    by_name = draw("icon", 48, 48, name="sun")
    assert same(by_name, draw("icon", 48, 48, bind="weather.icon", data={"weather": {"icon": "sun"}}))
    assert not same(by_name, draw("icon", 48, 48, bind="weather.icon", data={"weather": {"icon": "cloud"}}))
    # unknown/missing bound value falls back to name, then to the placeholder
    assert same(by_name, draw("icon", 48, 48, name="sun", bind="weather.icon", data={"weather": {"icon": "tornado"}}))
    assert same(by_name, draw("icon", 48, 48, name="sun", bind="weather.icon"))
    ph = draw("icon", 48, 48, bind="weather.icon")
    assert ink(ph) > 0 and not same(ph, by_name)
    assert same(ph, draw("icon", 48, 48, bind="weather.icon", data={"weather": {"icon": 7}}))


def test_icon_is_centred_in_non_square_box():
    t = draw("icon", 100, 24, name="bolt")
    assert ink(t, (0, 0, 38, 24)) == 0 and ink(t, (62, 0, 100, 24)) == 0


# ---- image -----------------------------------------------------------------------------------

@pytest.fixture
def assets(tmp_path):
    a = tmp_path / "assets"
    a.mkdir()
    grad = Image.linear_gradient("L").resize((100, 50))
    grad.save(a / "grad.png")
    Image.new("L", (40, 80), 0).save(a / "black.png")
    Image.new("RGBA", (20, 20), (0, 0, 0, 0)).save(a / "clear.png")
    (a / "junk.png").write_bytes(b"not an image")
    (a / "huge.png").write_bytes(png_bytes() + b"0" * (2 * 1024 * 1024))
    return a


def test_image_asset_fit_modes(assets):
    contain = draw("image", 100, 100, assets_dir=assets, src="black.png")          # 40x80 -> 50x100 centred
    assert ink(contain) == 50 * 100 and ink(contain, (0, 0, 25, 100)) == 0 and ink(contain, (75, 0, 100, 100)) == 0
    cover = draw("image", 100, 100, assets_dir=assets, src="black.png", fit="cover")
    assert ink(cover) == 100 * 100
    fill = draw("image", 100, 100, assets_dir=assets, src="black.png", fit="fill")
    assert ink(fill) == 100 * 100


def test_image_dither_modes(assets):
    fs = draw("image", 100, 50, assets_dir=assets, src="grad.png")
    th = draw("image", 100, 50, assets_dir=assets, src="grad.png", dither="threshold")
    assert not same(fs, th)
    # vertical gradient: threshold is a hard edge at mid grey, black on top, white below
    assert th.getpixel((50, 5)) == 0 and th.getpixel((50, 45)) == 255
    # floyd-steinberg mixes both colours in the middle band
    band = fs.crop((0, 20, 100, 30))
    assert 0 < ink(band) < 100 * 10
    assert same(fs, draw("image", 100, 50, assets_dir=assets, src="grad.png"))     # deterministic


def test_image_transparency_is_white(assets):
    assert ink(draw("image", 20, 20, assets_dir=assets, src="clear.png")) == 0


def test_image_failures_give_placeholder(assets):
    ph = draw("image", 100, 50, assets_dir=assets, src="nope.png")
    assert ink(ph) > 0
    for name in ("junk.png", "huge.png"):
        assert same(ph, draw("image", 100, 50, assets_dir=assets, src=name))
    assert same(ph, draw("image", 100, 50, src="grad.png"))                            # no assets dir at all


def test_image_pixel_limit(assets, monkeypatch):
    monkeypatch.setattr(WG, "MAX_IMAGE_PIXELS", 100)
    assert same(draw("image", 100, 50, assets_dir=assets, src="grad.png"), draw("image", 100, 50, src="x"))


def test_image_bound_paths(assets, tmp_path):
    p = assets / "black.png"
    key = "steam.now_playing.box_art_path"
    absolute = draw("image", 40, 80, bind=key, data={"steam.now_playing": {"box_art_path": str(p)}})
    assert ink(absolute) == 40 * 80
    rel = WG.render_widget({"type": "image", "x": 0, "y": 0, "w": 40, "h": 80, "bind": key},
                           DataSnapshot({"steam.now_playing": {"box_art_path": "assets/black.png"}}, base_dir=tmp_path))
    assert ink(rel) == 40 * 80
    ph = draw("image", 40, 80, bind=key)
    for bad in ({"box_art_path": "assets/black.png"},      # relative, no base dir
                {"box_art_path": ""}, {"box_art_path": 5}, {"box_art_path": "a\x00b"}, {"box_art_path": "/nonexistent.png"}):
        assert same(ph, draw("image", 40, 80, bind=key, data={"steam.now_playing": bad}))


def test_bound_image_size_limit_is_larger_than_package_limit(assets, monkeypatch):
    assert WG.MAX_BOUND_BYTES > WG.MAX_ASSET_BYTES
    monkeypatch.setattr(WG, "MAX_BOUND_BYTES", 10)
    key = "steam.now_playing.box_art_path"
    t = draw("image", 40, 80, bind=key, data={"steam.now_playing": {"box_art_path": str(assets / "black.png")}})
    assert ink(t) < 40 * 80


# ---- clock -----------------------------------------------------------------------------------

NOW = "2026-11-10T14:32:00+01:00"


def clock_text(snap=None, **o):
    opts = {"format": "%H:%M", "timezone": "", "bind": None, **o}
    return WG.Clock.text(opts, DataSnapshot({"time.now": NOW, **(snap or {})}))


def test_clock_formats():
    assert clock_text() == "14:32"
    assert clock_text(format="%d.%m.%Y") == "10.11.2026"
    assert clock_text(format="{settings.f}", snap={"settings": {"f": "%H"}}) == "14"


def test_clock_timezones():
    assert clock_text(timezone="America/New_York") == "08:32"
    assert clock_text(timezone="UTC") == "13:32"
    assert clock_text(snap={"time.tz": "Asia/Tokyo"}) == "22:32"          # time.tz applies to time.now
    assert clock_text(snap={"time.tz": "Asia/Tokyo"}, timezone="UTC") == "13:32"   # option wins


def test_clock_naive_and_epoch_inputs():
    assert clock_text(snap={"time.now": "2026-11-10T14:32:00"}) == "14:32"
    assert clock_text(snap={"time.now": "2026-11-10T14:32:00", "time.tz": "Asia/Tokyo"}) == "14:32"   # attach, not shift
    assert clock_text(snap={"time.now": 0}, timezone="UTC") == "00:00"
    assert clock_text(snap={"time.now": 3600}, timezone="Europe/Berlin") == "02:00"


def test_clock_bad_inputs_give_none():
    assert clock_text(snap={"time.now": "garbage"}) is None
    assert clock_text(snap={"time.now": None}) is None
    assert clock_text(timezone="Mars/Base") is None
    assert clock_text(snap={"time.tz": "Mars/Base"}) is None
    assert clock_text(timezone="{settings.tz}") is None                   # unset setting renders '--': unknown zone
    assert clock_text(format="") is None
    assert clock_text(snap={"time.now": "0001-01-01T00:00:00+14:00"}, timezone="UTC") is None   # overflow guarded


def test_clock_other_binding_ignores_time_tz():
    o = {"format": "%H:%M", "timezone": "", "bind": "weather.sunrise"}
    s = DataSnapshot({"weather": {"sunrise": "2026-11-10T07:30:00+01:00"}, "time.tz": "Asia/Tokyo"})
    assert WG.Clock.text(o, s) == "07:30"


def test_clock_widget_renders_and_falls_back():
    good = draw("clock", 300, 80, data={"time.now": NOW})
    ph = draw("clock", 300, 80, data={"time.now": None})
    assert ink(good) > 0 and ink(ph) > 0 and not same(good, ph)
    assert same(good, draw("clock", 300, 80, data={"time.now": NOW}))
    assert not same(good, draw("clock", 300, 80, data={"time.now": "2026-11-10T15:33:00+01:00"}))


def test_clock_strftime_error_is_caught(monkeypatch):
    class Boom:
        tzinfo = None

        def strftime(self, fmt):
            raise ValueError("bad")

    monkeypatch.setattr(DataSnapshot, "time", lambda self, path: Boom())
    assert clock_text() is None


# ---- now-playing -----------------------------------------------------------------------------

def playing(tmp_path, **np_over):
    art = tmp_path / "art.png"
    Image.new("L", (60, 90), 0).save(art)
    np = {"appid": 1, "name": "Game", "box_art_path": str(art), "session_minutes": 5, "total_minutes": 600,
          "last_achievement": "First Blood", **np_over}
    return {"steam.now_playing": np}


def test_now_playing_full_vs_empty(tmp_path):
    full = draw("now-playing", 600, 400, data=playing(tmp_path))
    empty = draw("now-playing", 600, 400)
    assert ink(full, (0, 0, 266, 400)) > ink(empty, (0, 0, 266, 400))        # art present
    assert ink(full) > 0 and ink(empty) > 0 and not same(full, empty)
    assert same(full, draw("now-playing", 600, 400, data=playing(tmp_path)))


def test_now_playing_art_width_and_achievement_toggle(tmp_path):
    d = playing(tmp_path)
    assert ink(draw("now-playing", 600, 400, data=d, art_width=0), (0, 350, 4, 400)) == 0     # no art frame
    narrow = draw("now-playing", 600, 400, data=d, art_width=100)
    assert narrow.getpixel((99, 200)) == 0 and narrow.getpixel((120, 200)) == 255
    with_ach = draw("now-playing", 600, 400, data=d)
    no_ach = draw("now-playing", 600, 400, data=d, show_achievement=False)
    assert ink(with_ach) > ink(no_ach)
    # art_width larger than half the box is capped
    assert same(draw("now-playing", 600, 400, data=d, art_width=640), draw("now-playing", 600, 400, data=d, art_width=300))


def test_now_playing_long_text_is_truncated(tmp_path):
    d = playing(tmp_path, name="A " + "very long game title " * 20, last_achievement="Achievement name " * 30)
    t = draw("now-playing", 600, 400, data=d)
    assert t.size == (600, 400) and ink(t, (300, 0, 600, 400)) > 0
    short = playing(tmp_path)
    assert not same(t, draw("now-playing", 600, 400, data=short))


def test_now_playing_missing_pieces_show_placeholders(tmp_path):
    d = playing(tmp_path, session_minutes=None, total_minutes="x", last_achievement=None, name=None)
    t = draw("now-playing", 600, 400, data=d)
    assert ink(t, (300, 0, 600, 400)) > 0
    assert not same(t, draw("now-playing", 600, 400, data=playing(tmp_path)))


def test_now_playing_bad_art_shows_placeholder(tmp_path):
    t = draw("now-playing", 600, 400, data=playing(tmp_path, box_art_path="/no/such.png"))
    assert ink(t, (20, 150, 260, 250)) > 0       # "--" centred in the art frame


def test_widget_registry_is_complete():
    assert set(WG.WIDGETS) == {"text", "number", "sparkline", "bar", "gauge", "icon", "image", "clock", "now-playing"}
    for w in WG.WIDGETS.values():
        for name, opt in w.options.items():
            assert opt.kind in {"int", "num", "bool", "str", "enum", "font", "range", "asset"}, (w.type, name)
    with pytest.raises(NotImplementedError):
        WG.Widget().draw(None, {})
