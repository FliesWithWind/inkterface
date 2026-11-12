"""idle/plan.py: user's idle screens -> one ISTR blob; failures become plan.error, never an exception."""
from pathlib import Path

import pytest

from inkterface_core import tz
from inkterface_core.idle.plan import build_plan, supported_modes
from inkterface_core.idle.store import parse_store
from inkterface_core.screens import load_screen

SCREENS = Path(__file__).resolve().parents[2] / "screens"


def scr(name):
    return name, load_screen(SCREENS / name)


def plan(*names, **kw):
    kw.setdefault("modes", {})
    kw.setdefault("rotate_s", 60)
    kw.setdefault("tz", "CET-1CEST,M3.5.0,M10.5.0/3")
    return build_plan([scr(n) for n in names], **kw)


def test_empty_selection_is_an_empty_plan():
    p = plan()
    assert p.blob is None and p.ids == () and p.error is None and p.size == 0 and p.digest == ""


def test_two_screens_rotate_and_carry_the_config():
    p = plan("minimal-clock", "steam-stats", lat=52.5, lon=-13.25, units="imperial", steam_key="K" * 32,
             steam_id="7" * 17, wifi_ssid="Home", wifi_pass="secretpass")
    s = parse_store(p.blob)
    assert p.ids == ("minimal-clock", "steam-stats") and s["n_layouts"] == 2 and s["rotate"] and s["rotate_s"] == 60
    assert s["config"] == {"tz": "CET-1CEST,M3.5.0,M10.5.0/3", "units": 1, "lat": "52.5", "lon": "-13.25",
                           "steam_key": "K" * 32, "steam_id": "7" * 17, "wifi_ssid": "Home", "wifi_pass": "secretpass"}
    assert "secretpass" not in repr(p) and "KKKK" not in repr(p) and len(p.digest) == 12


def test_rotate_off_and_a_single_screen_do_not_rotate():
    assert not parse_store(plan("minimal-clock", "steam-stats", rotate_s=0).blob)["rotate"]
    assert not parse_store(plan("minimal-clock").blob)["rotate"]
    assert parse_store(plan("minimal-clock", "steam-stats", rotate_s=10).blob)["rotate_s"] == 10


def test_unset_config_values_are_left_out():
    c = parse_store(plan("minimal-clock").blob)["config"]
    assert c == {"tz": "CET-1CEST,M3.5.0,M10.5.0/3", "units": 0}


def test_render_mode_override_changes_the_layout():
    mono, gray = plan("clock-weather", modes={"clock-weather": "mono"}), plan("clock-weather", modes={"clock-weather": "gray4"})
    assert mono.modes == ("mono",) and gray.modes == ("gray4",) and gray.needs_gray4 and not mono.needs_gray4
    assert mono.blob != gray.blob
    assert plan("clock-weather-gray4").modes == ("gray4",)   # no override: what the screen declares


def test_a_screen_that_does_not_compile_names_itself_and_yields_no_blob():
    n, s = scr("minimal-clock")
    s.idle = {"render_mode": "mono", "widgets": [{"type": "text", "x": 0, "y": 0, "w": 10, "h": 10, "text": "{nope.x}"}]}
    p = build_plan([(n, s)], modes={}, rotate_s=60, tz="UTC0")
    assert p.blob is None and p.error.startswith("minimal-clock: ") and p.ids == ("minimal-clock",)


def test_a_config_value_the_store_refuses_becomes_an_error_without_the_value():
    p = plan("minimal-clock", wifi_ssid="x" * 40, wifi_pass="topsecret")
    assert p.blob is None and "wifi_ssid" in p.error and "topsecret" not in p.error


def test_supported_modes():
    n, s = scr("clock-weather")
    assert set(supported_modes(s.idle, s.assets_dir)) == {"mono", "gray4"}
    assert supported_modes({"render_mode": "mono", "widgets": [{"type": "bogus"}]}, ".") == []


# -- tz.posix_tz ---------------------------------------------------------------------------------
@pytest.mark.skipif(tz.TzFile.from_name("Europe/Berlin") is None, reason="no system tz database")
class TestPosixTz:
    def test_named_zones(self):
        assert tz.posix_tz("Europe/Berlin") == "CET-1CEST,M3.5.0,M10.5.0/3"
        assert tz.posix_tz("Asia/Tokyo") == "JST-9"
        assert tz.posix_tz("UTC") == "UTC0"

    def test_system_zone_from_the_localtime_link(self, tmp_path, monkeypatch):
        monkeypatch.delenv("TZ", raising=False)
        link = tmp_path / "localtime"
        link.symlink_to(Path("/usr/share/zoneinfo/America/New_York"))
        assert tz.posix_tz(localtime=str(link)) == "EST5EDT,M3.2.0,M11.1.0"

    def test_unknown_or_hostile_names_and_garbage_files_fall_back_to_utc(self, tmp_path):
        for bad in ("No/Such_Zone", "../../etc/passwd", "/etc/passwd", ".hidden", "a\0b"):
            assert tz.posix_tz(bad) == "UTC0"
        junk = tmp_path / "lt"
        junk.write_bytes(b"not a tzif file\n")
        assert tz.posix_tz(localtime=str(junk)) == "UTC0"
        assert tz.posix_tz(localtime=str(tmp_path / "missing")) == "UTC0"

    def test_always_fits_the_panel_field(self):
        for name in ("Europe/Berlin", "America/St_Johns", "Australia/Lord_Howe", "Asia/Kolkata"):
            assert len(tz.posix_tz(name)) <= 63
