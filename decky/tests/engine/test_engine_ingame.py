"""The optional "In a game" profile: settings migration, profile selection, debounce, pin per profile, rotation reset."""
import json

import pytest

from inkterface_core.engine import Scheduler, Settings
from inkterface_core.engine.scheduler import SWITCH_DEBOUNCE_S

OLD = {"schema": 1, "rotation": ["telemetry", "big-clock"], "rotation_interval_s": 20, "pinned": None,
       "auto_now_playing": True, "now_playing_screen": "now-playing", "idle_timeout_s": 60, "ghost_clear_minutes": 10,
       "screen_settings": {"big-clock": {"time_format": "HH:mm"}}}
AV = ("a", "b", "c", "x", "y", "now-playing")


def mk(ingame=None, enabled=True, **kw):
    s = Settings(rotation=["a", "b", "c"], rotation_interval_s=10, ingame_enabled=enabled,
                 ingame=ingame if ingame is not None else {"rotation": ["x", "y"], "rotation_interval_s": 5, "pinned": None}, **kw)
    return Scheduler(s, AV)


# -- migration ----------------------------------------------------------------------
def test_old_file_loads_unchanged_and_saves_byte_for_byte(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps(OLD, indent=2) + "\n")
    before = p.read_bytes()
    s, err = Settings.load(p)
    assert err is None and s.ingame_enabled is False and s.ingame is None
    assert s.rotation == ["telemetry", "big-clock"] and s.rotation_interval_s == 20
    s.save(p)
    assert p.read_bytes() == before   # no new keys appear until the feature is used: an older plugin still reads the file


def test_ingame_roundtrip_and_kept_when_switched_off(tmp_path):
    p = tmp_path / "settings.json"
    ig = {"rotation": ["a"], "rotation_interval_s": 15, "pinned": "a"}
    Settings(ingame_enabled=True, ingame=ig).save(p)
    got, err = Settings.load(p)
    assert err is None and got.ingame_enabled and got.ingame == ig
    Settings(ingame_enabled=False, ingame=ig).save(p)
    assert json.loads(p.read_text())["ingame"] == ig   # off keeps the profile, so on again restores it


@pytest.mark.parametrize("bad", [
    {"ingame_enabled": "yes"},
    {"ingame": []},
    {"ingame": {"rotation": ["a"]}},   # missing keys
    {"ingame": {"rotation": ["a"], "rotation_interval_s": 10, "pinned": None, "extra": 1}},
    {"ingame": {"rotation": ["a", "a"], "rotation_interval_s": 10, "pinned": None}},
    {"ingame": {"rotation": ["A!"], "rotation_interval_s": 10, "pinned": None}},
    {"ingame": {"rotation": ["a"], "rotation_interval_s": 1, "pinned": None}},
    {"ingame": {"rotation": ["a"], "rotation_interval_s": True, "pinned": None}},
    {"ingame": {"rotation": ["a"], "rotation_interval_s": 10, "pinned": "../x"}},
])
def test_validate_rejects_bad_ingame(bad):
    errs = Settings.validate({**OLD, **bad})
    assert errs and all(e.startswith("ingame") for e in errs)


def test_profile_falls_back_to_normal_until_created():
    s = Settings(rotation=["a"], rotation_interval_s=7, pinned="a")
    assert s.profile("ingame") == s.profile("normal") == {"rotation": ["a"], "rotation_interval_s": 7, "pinned": "a"}


# -- selection ------------------------------------------------------------------------
def test_toggle_off_ignores_the_ingame_profile_and_keeps_now_playing():
    s = mk(enabled=False)
    assert s.current(0) == "a"
    s.set_now_playing(570)
    assert s.current(1) == "now-playing" and s.profile == "normal"   # today's behaviour exactly
    s.set_now_playing(None)
    assert s.current(2) == "a"


def test_game_selects_ingame_profile_within_one_call_and_exit_returns():
    s = mk()
    assert s.current(0) == "a" and s.profile == "normal"
    s.set_now_playing(570)
    assert s.current(0.02) == "x" and s.profile == "ingame"   # auto now-playing is not used: the profile decides
    assert s.current(5.02) == "y"   # the in-game interval (5 s)
    s.set_now_playing(None)
    assert s.current(10) == "a" and s.profile == "normal"   # rotation restarts at its first screen


def test_rotation_index_resets_on_switch():
    s = mk()
    s.current(0)
    assert s.current(21) == "c"
    s.set_now_playing(1)
    assert s.current(22) == "x"
    assert s.current(26.9) == "x" and s.current(27) == "y"   # the slot clock starts at the switch
    s.set_now_playing(None)
    assert s.current(30) == "a" and s.current(39.9) == "a" and s.current(40) == "b"


def test_debounce_one_switch_per_window():
    s = mk()
    s.current(0)
    s.set_now_playing(1)
    assert s.current(100) == "x"            # first switch is immediate
    s.set_now_playing(None)
    assert not s.switch_pending(101)
    assert s.current(101) == "x" and s.profile == "ingame"   # game gone within 3 s: stays, no flicker
    s.set_now_playing(1)
    assert not s.switch_pending(102)        # back to what it shows: nothing pending at all
    s.set_now_playing(None)
    assert not s.switch_pending(100 + SWITCH_DEBOUNCE_S - 0.01)
    assert s.switch_pending(100 + SWITCH_DEBOUNCE_S)
    assert s.current(100 + SWITCH_DEBOUNCE_S) == "a" and s.profile == "normal"


def test_rapid_flapping_switches_at_most_once_per_window():
    s = mk()
    switches, last, t = 0, s.select(0), 0.0
    for i in range(200):   # the game toggles every 0.1 s for 20 s
        s.set_now_playing(1 if i % 2 == 0 else None)
        t = i * 0.1
        s.current(t)
        if s.profile != last:
            switches, last = switches + 1, s.profile
    assert switches <= 20 / SWITCH_DEBOUNCE_S + 1


def test_pin_per_profile():
    s = mk(ingame={"rotation": ["x", "y"], "rotation_interval_s": 5, "pinned": "y"}, pinned="b")
    assert s.current(0) == "b"            # normal profile's pin
    s.set_now_playing(1)
    assert s.current(1) == "y"            # in-game profile's own pin
    s.settings.ingame["pinned"] = None
    assert s.current(2) == "x"            # unpinned: its rotation
    s.set_now_playing(None)
    assert s.current(10) == "b"


def test_ingame_pin_to_unavailable_screen_falls_back_to_its_rotation():
    s = mk(ingame={"rotation": ["x"], "rotation_interval_s": 5, "pinned": "gone"})
    s.set_now_playing(1)
    assert s.current(0) == "x"


def test_enabling_mid_game_switches_and_disabling_returns():
    s = mk(enabled=False)
    s.set_now_playing(1)
    assert s.current(0) == "now-playing"
    s.update(Settings(**{**s.settings.__dict__, "ingame_enabled": True}), 1)
    assert s.current(1) == "x" and s.profile == "ingame"
    s.update(Settings(**{**s.settings.__dict__, "ingame_enabled": False}), 10)
    assert s.current(10) == "now-playing" and s.profile == "normal"


def test_update_keeps_the_ingame_rotation_position():
    s = mk()
    s.set_now_playing(1)
    s.current(0)
    assert s.current(5) == "y"
    s.update(Settings(**{**s.settings.__dict__, "ingame": {"rotation": ["c", "y"], "rotation_interval_s": 5, "pinned": None}}), 6)
    assert s.current(6) == "y" and s.profile == "ingame"


# -- engine: status field, one tick, debounced switch renders on time ------------------------------
from engine_testlib import Rig  # noqa: E402


def ig_rig(tmp_path, enabled=True, pinned=None):
    return Rig(tmp_path, Settings(rotation=["a"], ingame_enabled=enabled,
                                  ingame={"rotation": ["now-playing"], "rotation_interval_s": 30, "pinned": pinned}))


def test_status_reports_profile_and_switch_is_within_one_tick(tmp_path):
    r = ig_rig(tmp_path)
    r.run(2)
    assert r.st["profile"] == "normal" and r.st["current_screen"] == "a"
    r.engine.set_now_playing(570)
    r.engine.step()
    assert r.st["current_screen"] == "now-playing" and r.st["profile"] == "ingame"
    r.run(3)
    assert r.panel.glass == bytearray(r.expected("now-playing"))


def test_status_profile_is_right_while_disconnected(tmp_path):
    r = ig_rig(tmp_path)
    r.plugged = False
    r.run(1)
    r.engine.set_now_playing(1)
    assert r.st["profile"] == "ingame" and r.st["state"] == "disconnected"


def test_status_profile_toggle_off_is_always_normal(tmp_path):
    r = ig_rig(tmp_path, enabled=False)
    r.run(2)
    r.engine.set_now_playing(570)
    r.run(2)
    assert r.st["profile"] == "normal" and r.st["current_screen"] == "now-playing"   # today's auto now-playing


def test_debounced_switch_back_renders_when_the_window_ends(tmp_path):
    r = ig_rig(tmp_path)
    r.run(2)
    r.engine.set_now_playing(1)
    r.run(1)
    assert r.st["current_screen"] == "now-playing"
    r.engine.set_now_playing(None)   # exits 1 s later: held for the rest of the 3 s window
    r.run(1.5)
    assert r.st["current_screen"] == "now-playing" and r.st["profile"] == "ingame"
    r.run(0.6)   # window over at 3.0 s: switched without waiting for now-playing's 5 s refresh deadline
    assert r.st["current_screen"] == "a" and r.st["profile"] == "normal"


def test_pinned_status_is_the_active_profiles_pin(tmp_path):
    r = ig_rig(tmp_path, pinned="b")
    r.run(2)
    assert r.st["pinned"] is None
    r.engine.set_now_playing(1)
    r.run(1)
    assert r.st["pinned"] == "b" and r.st["current_screen"] == "b"
