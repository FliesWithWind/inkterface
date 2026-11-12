"""Pixel-exact goldens for the built-in idle packages, rendered by the real firmware renderer
(firmware/src/ee05/idle_render.cpp) against sample-data.json. After an intended visual change:
    INKTERFACE_UPDATE_GOLDENS=1 python3 -m pytest decky/tests/idle/test_goldens.py
and LOOK at the PNG diff before committing."""
import json
import os
import shutil

import pytest
from PIL import Image

import idlerender as R

SCREENS = os.path.join(R.ROOT, "decky", "screens")
GOLD = os.path.join(os.path.dirname(__file__), "goldens")
IDS = ["clock-weather", "clock-weather-gray4", "steam-stats", "minimal-clock"]

pytestmark = pytest.mark.skipif(not R.have_gpp(), reason="g++ not available")


@pytest.fixture(scope="module")
def work(tmp_path_factory):
    return str(tmp_path_factory.mktemp("render"))


@pytest.mark.parametrize("sid", IDS)
def test_golden(sid, work, tmp_path):
    out = str(tmp_path / f"{sid}.png")
    R.render_screen(os.path.join(SCREENS, sid), out, work)
    gold = os.path.join(GOLD, f"{sid}.png")
    if os.environ.get("INKTERFACE_UPDATE_GOLDENS"):
        os.makedirs(GOLD, exist_ok=True)
        shutil.copy(out, gold)
    assert os.path.exists(gold), "no golden: run with INKTERFACE_UPDATE_GOLDENS=1 and review it"
    a, b = Image.open(out), Image.open(gold)
    assert a.size == b.size == (648, 480)
    assert a.mode == b.mode
    assert a.tobytes() == b.tobytes(), f"{sid} differs from its golden"


@pytest.mark.parametrize("sid", IDS)
def test_render_modes(sid, work, tmp_path):
    """mono layouts give a 1-bit image, gray4 a 2-bit one (only 4 distinct levels)."""
    out = str(tmp_path / "x.png")
    R.render_screen(os.path.join(SCREENS, sid), out, work)
    mode = json.load(open(os.path.join(SCREENS, sid, "screen.json")))["idle"]["render_mode"]
    im = Image.open(out)
    if mode == "mono":
        assert im.mode == "1"
    else:
        assert im.mode == "L" and set(v for _, v in im.getcolors()) <= {0, 85, 170, 255}
        assert len(im.getcolors()) >= 3  # real grays used, not just black and white


def test_missing_data_draws_placeholders(work, tmp_path):
    """No sample values at all: renders, and never crashes (placeholders)."""
    out = str(tmp_path / "x.png")
    R.render_screen(os.path.join(SCREENS, "clock-weather"), out, work,
                    sample_override={"epoch": 0, "tz": "UTC0", "values": {}})
    assert Image.open(out).getcolors() is not None
