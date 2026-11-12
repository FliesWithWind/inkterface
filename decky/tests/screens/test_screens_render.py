import json
import logging

import pytest
from PIL import Image
from screens_testlib import ink, valid_doc

from inkterface_core import HEIGHT, WIDTH, render_test_frame
from inkterface_core.screens import (DataSnapshot, ScreenError, load_screen, pack, render, render_frame, unpack,
                                     validate_screen)
from inkterface_core.screens import data as D
from inkterface_core.screens import widgets as WG
from inkterface_core.screens.renderer import FRAME_BYTES

BUILTIN_IDS = ["big-clock", "now-playing", "telemetry", "thermals-power"]


@pytest.fixture(scope="module")
def builtin_dir():
    from conftest import BUILTIN_DIR  # noqa: PLC0415 - resolved lazily, conftest is not a package
    return BUILTIN_DIR


def builtin_snapshot(pkg):
    f = pkg / "sample-data.json"
    return DataSnapshot(json.loads(f.read_text()), f.parent)


# ---- frame packing ------------------------------------------------------------------------------

def test_pack_matches_frame_py_layout():
    img = Image.new("1", (WIDTH, HEIGHT), 255)
    img.putpixel((0, 0), 0)          # black, first bit of byte 0
    img.putpixel((9, 1), 0)          # black, second bit of byte 1 in row 1
    b = pack(img)
    assert len(b) == FRAME_BYTES == 38880 == len(render_test_frame())
    assert b[0] == 0b01111111 and b[81 + 1] == 0b10111111
    assert b[2] == 0xFF
    assert pack(Image.frombytes("1", (WIDTH, HEIGHT), render_test_frame())) == render_test_frame()   # same bit order


def test_pack_unpack_roundtrip_and_validation():
    img = Image.new("1", (WIDTH, HEIGHT), 255)
    img.paste(0, (100, 100, 200, 200))
    assert unpack(pack(img)).tobytes() == img.tobytes()
    with pytest.raises(ValueError):
        pack(Image.new("L", (WIDTH, HEIGHT)))
    with pytest.raises(ValueError):
        pack(Image.new("1", (10, 10)))
    with pytest.raises(ValueError):
        unpack(b"123")


# ---- render -------------------------------------------------------------------------------------

def make_screen(make_pkg, widgets, settings=None, **extra):
    doc = valid_doc(**extra)
    doc["active"] = {"widgets": widgets}
    if settings:
        doc["settings"] = settings
    return load_screen(make_pkg(doc))


def test_render_size_mode_and_background(make_pkg):
    s = make_screen(make_pkg, [{"type": "text", "x": 10, "y": 10, "w": 100, "h": 30, "text": "Hi"}])
    img = render(s, {})
    assert img.size == (648, 480) and img.mode == "1"
    assert ink(img, (200, 200, 648, 480)) == 0 and ink(img, (10, 10, 110, 40)) > 0
    assert len(render_frame(s, {})) == FRAME_BYTES


def test_widgets_are_clipped_to_their_box_and_placed_at_xy(make_pkg):
    s = make_screen(make_pkg, [{"type": "text", "x": 100, "y": 50, "w": 40, "h": 20, "text": "A very long clipped text", "font": "bold-32"}])
    img = render(s, {})
    assert ink(img) == ink(img, (100, 50, 140, 70)) > 0


def test_later_widgets_draw_over_earlier(make_pkg):
    s = make_screen(make_pkg, [{"type": "text", "x": 0, "y": 0, "w": 50, "h": 50, "text": "", "invert": True},
                               {"type": "text", "x": 0, "y": 0, "w": 25, "h": 25, "text": ""}])
    img = render(s, {})
    assert img.getpixel((10, 10)) == 255 and img.getpixel((40, 40)) == 0


def test_render_is_deterministic_across_data_types(make_pkg):
    s = make_screen(make_pkg, [{"type": "number", "x": 0, "y": 0, "w": 100, "h": 30, "bind": "system.cpu.load"}])
    d = {"system.cpu.load": 12}
    assert render_frame(s, d) == render_frame(s, DataSnapshot(d)) == render_frame(s, dict(d))
    assert render_frame(s, d) != render_frame(s, {"system.cpu.load": 13})
    assert render_frame(s, None) == render_frame(s, {})


def test_render_with_settings(make_pkg):
    s = make_screen(make_pkg, [{"type": "text", "x": 0, "y": 0, "w": 200, "h": 30, "text": "{settings.title}"}],
                    settings=[{"key": "title", "type": "string", "default": "Default"}])
    assert render_frame(s, {}) == render_frame(s, {}, {"title": "Default"})
    assert render_frame(s, {}) != render_frame(s, {}, {"title": "Other"})
    with pytest.raises(ScreenError, match="settings.nope: unknown setting"):
        render(s, {}, {"nope": 1})
    with pytest.raises(ScreenError, match="must be string"):
        render(s, {}, {"title": 5})


def test_missing_everything_still_renders(builtin_dir):
    for pid in BUILTIN_IDS:
        img = render(load_screen(builtin_dir / pid), {})
        assert img.size == (648, 480) and ink(img) > 0


def test_a_crashing_widget_shows_placeholder_and_others_survive(make_pkg, monkeypatch, caplog):
    s = make_screen(make_pkg, [{"type": "text", "x": 0, "y": 0, "w": 100, "h": 30, "text": "ok"},
                               {"type": "number", "x": 0, "y": 100, "w": 100, "h": 30, "bind": "system.cpu.load"}])

    def boom(self, ctx, o):
        raise RuntimeError("kaput")

    monkeypatch.setattr(WG.Number, "draw", boom)
    with caplog.at_level(logging.ERROR):
        img = render(s, {"system.cpu.load": 1})
    assert ink(img, (0, 0, 100, 30)) > 0 and ink(img, (0, 100, 100, 130)) > 0
    assert "kaput" in caplog.text and "widget 1 (number) failed" in caplog.text


# ---- built-in packages -------------------------------------------------------------------------------

@pytest.mark.parametrize("pid", BUILTIN_IDS)
def test_builtin_is_valid_and_documented(builtin_dir, pid):
    s = load_screen(builtin_dir / pid)
    assert s.id == pid and s.author and s.description and s.active and s.version == "1.0.0"
    assert validate_screen(json.loads((builtin_dir / pid / "screen.json").read_text()), builtin_dir / pid, pid) == []


@pytest.mark.parametrize("pid", BUILTIN_IDS)
def test_builtin_sample_data_covers_every_binding(builtin_dir, pid):
    """No '--' placeholder in the example render: every bound source has a sample value."""
    s = load_screen(builtin_dir / pid)
    snap = builtin_snapshot(builtin_dir / pid).with_settings(s.resolve_settings())
    for i, w in enumerate(s.active["widgets"]):
        refs = [w["bind"]] if "bind" in w else []
        for k, v in w.items():
            if k in WG.WIDGETS[w["type"]].options and WG.WIDGETS[w["type"]].options[k].templated:
                refs += [p[0] for p in D.parse_template(v) if isinstance(p, tuple)]
        if w["type"] == "clock":
            refs.append("time.now")
        if w["type"] == "now-playing":
            refs += ["steam.now_playing.name", "steam.now_playing.box_art_path", "steam.now_playing.session_minutes",
                     "steam.now_playing.total_minutes", "steam.now_playing.last_achievement"]
        for r in refs:
            assert snap.get(r) is not None, f"{pid} widget {i}: no sample value for {r}"


IDLE_IDS = ["clock-weather", "clock-weather-gray4", "minimal-clock", "steam-stats"]


def test_four_builtin_active_screens_exist(builtin_dir):
    pkgs = [p for p in builtin_dir.iterdir() if (p / "screen.json").is_file()]
    assert sorted(p.name for p in pkgs if load_screen(p).active) == BUILTIN_IDS


def test_four_builtin_idle_screens_exist(builtin_dir):
    pkgs = [p for p in builtin_dir.iterdir() if (p / "screen.json").is_file()]
    assert sorted(p.name for p in pkgs if load_screen(p).idle) == IDLE_IDS
    assert all(load_screen(builtin_dir / i).active is None for i in IDLE_IDS)


@pytest.mark.parametrize("pid", BUILTIN_IDS)
def test_builtin_golden(builtin_dir, update_goldens, pid):
    from conftest import GOLDEN_DIR  # noqa: PLC0415
    pkg = builtin_dir / pid
    img = render(load_screen(pkg), builtin_snapshot(pkg))
    golden = GOLDEN_DIR / f"{pid}.png"
    if update_goldens:
        GOLDEN_DIR.mkdir(exist_ok=True)
        img.save(golden)
    assert golden.is_file(), f"missing golden {golden}; run: pytest decky/tests/screens --update-goldens"
    expected = Image.open(golden).convert("1")
    assert expected.size == img.size
    assert img.tobytes() == expected.tobytes(), f"{pid} differs from {golden.name}; inspect, then --update-goldens"
    assert render_frame(load_screen(pkg), builtin_snapshot(pkg)) == expected.tobytes()


def test_builtin_settings_change_render(builtin_dir):
    pkg = builtin_dir / "big-clock"
    s, snap = load_screen(pkg), builtin_snapshot(pkg)
    base = render_frame(s, snap)
    assert base != render_frame(s, snap, {"time_format": "%I:%M"})
    assert base != render_frame(s, snap, {"date_format": "%d/%m/%Y"})
