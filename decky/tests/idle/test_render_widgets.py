"""Widget-level checks of the firmware renderer (real idle_render.cpp through the host harness):
alignment, clipping, ellipsis, wrap, number/unit/decimals, bar, invert, border, icons, image,
clock/template equivalence, gray levels, dark layouts, placeholders for missing data."""
import os

import pytest
from PIL import Image

import idlerender as R

pytestmark = pytest.mark.skipif(not R.have_gpp(), reason="g++ not available")

EPOCH = 1794316020  # 2026-11-10 14:07 CET
CET = "CET-1CEST,M3.5.0,M10.5.0/3"
SRCS = {"time": {"type": "clock"}, "wx": {"type": "weather"},
        "api": {"type": "http_json", "url": "https://x.example/a.json", "pointer": "/v"}}


@pytest.fixture(scope="module")
def work(tmp_path_factory):
    return str(tmp_path_factory.mktemp("rw"))


def draw(work, widgets, values=None, *, mode="mono", epoch=EPOCH, sources=None, **idle_kw):
    idle = {"render_mode": mode, "sources": sources or SRCS, "widgets": widgets, **idle_kw}
    out = os.path.join(work, "o.png")
    R.render_idle(idle, {"epoch": epoch, "tz": CET, "values": values or {}}, out, work)
    return Image.open(out).convert("L")


def ink_box(im, box=None):
    """bounding box of non-white pixels (optionally inside box)"""
    sub = im.crop(box) if box else im
    b = sub.point(lambda v: 255 if v < 255 else 0).getbbox()
    if b and box:
        b = (b[0] + box[0], b[1] + box[1], b[2] + box[0], b[3] + box[1])
    return b


def ink_count(im, box=None):
    sub = im.crop(box) if box else im
    return sum(1 for v in sub.get_flattened_data() if v < 128)


def text(x, y, w, h, t, **kw):
    d = {"type": "text", "x": x, "y": y, "w": w, "h": h, "font": "regular-22", "text": t}
    d.update(kw)
    return d


def test_alignment(work):
    box = (100, 50, 400, 40)
    bl = ink_box(draw(work, [text(*box, "Hi", align="left")]))
    bc = ink_box(draw(work, [text(*box, "Hi", align="center")]))
    br = ink_box(draw(work, [text(*box, "Hi", align="right")]))
    assert bl[0] <= 100 + 4 and br[2] >= 500 - 4
    assert abs((bc[0] + bc[2]) / 2 - 300) <= 4
    assert bl[0] < bc[0] < br[0]


def test_valign(work):
    box = (0, 100, 300, 120)
    t = ink_box(draw(work, [text(*box, "Hi", valign="top")]))
    m = ink_box(draw(work, [text(*box, "Hi", valign="middle")]))
    b = ink_box(draw(work, [text(*box, "Hi", valign="bottom")]))
    assert t[1] < m[1] < b[1] and b[3] <= 220 and t[1] >= 100


def test_nothing_outside_the_widget_box(work):
    long = "A very long line of text that cannot possibly fit in this tiny box"
    for kw in ({}, {"ellipsis": False}, {"wrap": True}):
        b = ink_box(draw(work, [text(200, 200, 90, 30, long, **kw)]))
        assert b and b[0] >= 200 and b[2] <= 290 and b[1] >= 200 and b[3] <= 230, (kw, b)


def test_ellipsis_vs_clip(work):
    long = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    e = draw(work, [text(0, 0, 150, 30, long)])
    c = draw(work, [text(0, 0, 150, 30, long, ellipsis=False)])
    assert ink_box(e)[2] <= 150          # ellipsized text fits in the box (dots included)
    assert ink_box(c)[2] >= 148          # clipped text runs to the edge
    assert e.tobytes() != c.tobytes()


def test_wrap_and_max_lines(work):
    t = "one two three four five six seven eight nine ten eleven twelve"
    one = ink_box(draw(work, [text(0, 0, 150, 200, t, wrap=False)]))
    wrapped = ink_box(draw(work, [text(0, 0, 150, 200, t, wrap=True)]))
    capped = ink_box(draw(work, [text(0, 0, 150, 200, t, wrap=True, max_lines=2)]))
    assert one[3] < wrapped[3]
    assert capped[3] < wrapped[3]
    assert capped[3] <= 2 * 30 + 4       # two lines of the 22px font (line box ~29)


def test_template_equals_literal(work):
    a = draw(work, [text(10, 10, 300, 40, "T={wx.current.temp:.0f}C {wx.current.condition} {wx.forecast[1].day}")],
             {"wx.current.temp": 11.6, "wx.current.condition": "Fog", "wx.forecast[1].day": "THU"})
    b = draw(work, [text(10, 10, 300, 40, "T=12C Fog THU")])
    assert a.tobytes() == b.tobytes()
    esc = ink_box(draw(work, [text(10, 10, 300, 40, "{{x}}")]))        # literal braces: "{x}" = 3 glyphs
    one = ink_box(draw(work, [text(10, 10, 300, 40, "x")]))
    assert esc[2] - esc[0] > one[2] - one[0] + 12


def test_missing_data_placeholder(work):
    t = draw(work, [text(10, 10, 300, 40, "T={wx.current.temp:.0f}")])
    ph = draw(work, [text(10, 10, 300, 40, "T=--")])
    assert t.tobytes() == ph.tobytes()
    n = draw(work, [{"type": "number", "x": 0, "y": 0, "w": 300, "h": 60, "bind": "wx.current.temp",
                     "font": "bold-36", "unit": "C", "valign": "middle"}])
    p = draw(work, [text(0, 0, 300, 60, "--", font="bold-36", valign="middle")])
    assert n.tobytes() == p.tobytes()       # no value: just "--", no unit


def num(**kw):
    d = {"type": "number", "x": 0, "y": 0, "w": 300, "h": 60, "bind": "wx.current.temp", "font": "bold-36",
         "valign": "middle"}
    d.update(kw)
    return d


def lit(work, s):
    return draw(work, [text(0, 0, 300, 60, s, font="bold-36", valign="middle")]).tobytes()


def test_number_decimals_unit_and_default_format(work):
    v = {"wx.current.temp": 3.14159}
    assert draw(work, [num(format="{:.2f}")], v).tobytes() == lit(work, "3.14")
    assert draw(work, [num(format="{:.0f}")], v).tobytes() == lit(work, "3")
    assert draw(work, [num()], {"wx.current.temp": 12.0}).tobytes() == lit(work, "12")
    assert draw(work, [num()], {"wx.current.temp": 12.34}).tobytes() == lit(work, "12.3")
    assert ink_box(draw(work, [num(unit="kg")], v))[2] > ink_box(draw(work, [num()], v))[2]   # unit adds width
    assert draw(work, [num(format="{:.0f}")], {"wx.current.temp": -0.2}).tobytes() == lit(work, "0")  # never "-0"
    assert draw(work, [num(format="{:.0f}")], {"wx.current.temp": -7.0}).tobytes() == lit(work, "-7")


def test_bar(work):
    def bar(**kw):
        d = {"type": "bar", "x": 10, "y": 10, "w": 200, "h": 24, "bind": "wx.today.rain", "range": [0, 100]}
        d.update(kw)
        return d
    box = (10, 10, 210, 34)
    counts = [ink_count(draw(work, [bar()], {"wx.today.rain": v}), box) for v in (0, 25, 50, 100)]
    assert counts == sorted(counts) and len(set(counts)) == 4
    assert ink_count(draw(work, [bar()], {"wx.today.rain": 500}), box) == counts[-1]    # clamped high
    assert ink_count(draw(work, [bar()], {"wx.today.rain": -50}), box) == counts[0]     # clamped low
    assert ink_count(draw(work, [bar()], {}), box) == counts[0]                         # no data: empty frame
    vert = draw(work, [bar(w=24, h=200, orientation="vertical")], {"wx.today.rain": 50})
    top = ink_count(vert, (12, 12, 32, 108))
    bottom = ink_count(vert, (12, 112, 32, 208))
    assert bottom > 0 and top == 0           # vertical bars fill from the bottom


def test_invert_and_border(work):
    inv = draw(work, [text(50, 50, 100, 40, "", invert=True)])
    assert ink_count(inv, (50, 50, 150, 90)) == 100 * 40
    assert ink_box(inv) == (50, 50, 150, 90)
    knock = draw(work, [text(50, 50, 100, 40, "Hi", invert=True)])
    assert 0 < ink_count(knock, (50, 50, 150, 90)) < 100 * 40         # white text knocked out of black
    b = draw(work, [text(50, 50, 100, 40, "", border=3)])
    assert ink_box(b) == (50, 50, 150, 90)
    assert ink_count(b, (50, 50, 150, 90)) == 100 * 40 - 94 * 34
    assert b.getpixel((52, 60)) == 0 and b.getpixel((53, 60)) == 255


def test_icons(work):
    def icon(code):
        return draw(work, [{"type": "icon", "x": 100, "y": 100, "w": 96, "h": 96, "bind": "wx.current.code",
                            "set": "weather"}], {"wx.current.code": code})
    imgs = {c: icon(c).tobytes() for c in (0, 2, 3, 45, 61, 73, 95)}
    assert len(set(imgs.values())) == 7                        # every family looks different
    assert icon(1).tobytes() == icon(0).tobytes() and icon(81).tobytes() == icon(61).tobytes()
    for c in (0, 3, 95):
        b = ink_box(icon(c))
        assert b[0] >= 100 and b[2] <= 196 and b[1] >= 100 and b[3] <= 196
    ph = draw(work, [{"type": "icon", "x": 100, "y": 100, "w": 96, "h": 96, "bind": "wx.current.code", "set": "weather"}])
    assert 0 < ink_count(ph) < 200                              # missing data: a small dash, not an icon
    named = {n: draw(work, [{"type": "icon", "x": 10, "y": 10, "w": 64, "h": 64, "name": n}]).tobytes()
             for n in ("sun", "cloud", "rain", "snow", "storm", "fog", "clock", "gamepad")}
    assert len(set(named.values())) == 8
    big = ink_count(draw(work, [{"type": "icon", "x": 0, "y": 0, "w": 128, "h": 128, "name": "sun"}]))
    small = ink_count(draw(work, [{"type": "icon", "x": 0, "y": 0, "w": 32, "h": 32, "name": "sun"}]))
    assert big > 4 * small                                      # scales with the box


def test_image_widget(work, tmp_path):
    src = Image.new("L", (16, 8), 255)
    for x in range(16):
        for y in range(8):
            if (x // 2 + y // 2) % 2 == 0:
                src.putpixel((x, y), 0)
    assets = tmp_path / "assets"
    assets.mkdir()
    src.save(assets / "chk.png")
    idle = {"render_mode": "mono", "sources": {},
            "widgets": [{"type": "image", "x": 40, "y": 40, "w": 16, "h": 8, "src": "chk.png"}]}
    out = os.path.join(work, "img.png")
    R.render_idle(idle, {}, out, work, assets_dir=str(assets))
    im = Image.open(out).convert("L")
    for x in range(16):
        for y in range(8):
            assert (im.getpixel((40 + x, 40 + y)) == 0) == (src.getpixel((x, y)) == 0)
    assert ink_box(im) == (40, 40, 56, 48)


def test_clock_equals_text(work):
    def clock(fmt, w=300, **kw):
        d = {"type": "clock", "x": 0, "y": 0, "w": w, "h": 60, "bind": "time.now", "format": fmt, "font": "bold-36"}
        d.update(kw)
        return d
    a = draw(work, [clock("%H:%M")])
    assert a.tobytes() == draw(work, [text(0, 0, 300, 60, "14:07", font="bold-36")]).tobytes()   # CET 14:07
    full = draw(work, [clock("%A %d %B %Y %I:%M %p %j %y %e %a %b %%", 640, font="regular-14")])
    expect = draw(work, [text(0, 0, 640, 60, "Tuesday 10 November 2026 02:07 PM 314 26 10 Tue Nov %", font="regular-14")])
    assert full.tobytes() == expect.tobytes()
    un = draw(work, [clock("%H:%M")], epoch=0)           # clock not synced: placeholders, literals kept
    assert un.tobytes() == draw(work, [text(0, 0, 300, 60, "--:--", font="bold-36")]).tobytes()
    tzs = dict(SRCS)
    tzs["time"] = {"type": "clock", "tz": "UTC0"}        # per-source zone override beats the global one
    assert draw(work, [clock("%H:%M")], sources=tzs).tobytes() == \
        draw(work, [text(0, 0, 300, 60, "13:07", font="bold-36")]).tobytes()


def test_gray4_levels_and_dark(work):
    g = draw(work, [text(10, 10, 200, 40, "Gray", color=1), text(10, 60, 200, 40, "Gray", color=2),
                    text(10, 110, 200, 40, "Black")], mode="gray4")
    assert set(g.get_flattened_data()) == {0, 85, 170, 255}
    assert g.crop((10, 10, 210, 50)).getextrema()[0] == 85
    assert g.crop((10, 60, 210, 100)).getextrema()[0] == 170
    d = draw(work, [text(10, 10, 200, 40, "Hi", color=3)], dark=True)
    assert d.getpixel((600, 400)) == 0                   # dark layout: black background
    assert ink_count(d.point(lambda v: 255 - v), (10, 10, 210, 50)) > 0      # white text on black
    fill = draw(work, [{"type": "bar", "x": 10, "y": 10, "w": 100, "h": 40, "bind": "wx.today.rain", "range": [0, 100],
                        "color": 1}], {"wx.today.rain": 100})
    assert {fill.getpixel((x, y)) for x in range(14, 106) for y in range(14, 46)} == {0, 255}  # gray dithers in mono


def test_widgets_paint_in_order(work):
    a = draw(work, [text(0, 0, 100, 40, "", invert=True), text(0, 0, 100, 40, "X", invert=True, color=3)])
    b = draw(work, [text(0, 0, 100, 40, "X", invert=True, color=3), text(0, 0, 100, 40, "", invert=True)])
    assert a.tobytes() != b.tobytes()
    assert ink_count(b, (0, 0, 100, 40)) == 100 * 40     # the later solid widget covers the earlier text


def test_hostile_values_do_not_crash(work):
    """huge / negative / non-finite looking values: still renders something sane inside the boxes"""
    big = {"wx.current.temp": 1e300, "wx.current.code": 1e300, "wx.today.rain": -1e300, "wx.current.condition": "x" * 80}
    ws = [num(), {"type": "icon", "x": 100, "y": 100, "w": 64, "h": 64, "bind": "wx.current.code", "set": "weather"},
          {"type": "bar", "x": 10, "y": 200, "w": 200, "h": 24, "bind": "wx.today.rain", "range": [0, 100]},
          text(0, 300, 300, 30, "{wx.current.condition}")]
    im = draw(work, ws, big)
    assert ink_box(im, (0, 300, 300, 330)) is not None
    assert ink_box(im)[2] <= 648
