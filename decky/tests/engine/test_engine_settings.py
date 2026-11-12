import json
import os

import pytest

from inkterface_core.engine import Settings


def test_defaults():
    s = Settings()
    assert s.rotation == ["telemetry", "thermals-power", "big-clock"]
    assert (s.rotation_interval_s, s.idle_timeout_s, s.ghost_clear_minutes) == (30, 60, 10)
    assert s.pinned is None and s.auto_now_playing is True and s.now_playing_screen == "now-playing"
    assert s.screen_settings == {}
    assert Settings.validate(s.to_dict()) == []


def test_defaults_are_not_shared_between_instances():
    a, b = Settings(), Settings()
    a.rotation.append("x")
    a.screen_settings["x"] = {}
    assert b.rotation == ["telemetry", "thermals-power", "big-clock"] and b.screen_settings == {}


def test_roundtrip(tmp_path):
    p = tmp_path / "sub" / "settings.json"
    s = Settings(rotation=["a", "b"], rotation_interval_s=12, pinned="a", auto_now_playing=False, now_playing_screen="np",
                 idle_timeout_s=90, ghost_clear_minutes=0, screen_settings={"a": {"k": 1}})
    s.save(p)  # creates the directory
    got, err = Settings.load(p)
    assert err is None and got == s
    assert json.loads(p.read_text())["schema"] == 1


def test_save_is_atomic_and_leaves_no_temp_files(tmp_path, monkeypatch):
    p = tmp_path / "settings.json"
    Settings(pinned="a").save(p)
    before = p.read_text()

    def boom(*a):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        Settings(pinned="b").save(p)
    assert p.read_text() == before  # old file intact
    assert os.listdir(tmp_path) == ["settings.json"]  # temp file removed


def test_save_cleanup_tolerates_unlink_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(OSError("x")))
    monkeypatch.setattr(os, "unlink", lambda *a: (_ for _ in ()).throw(OSError("y")))
    with pytest.raises(OSError, match="x"):
        Settings().save(tmp_path / "s.json")


def test_missing_file_is_defaults_without_error(tmp_path):
    s, err = Settings.load(tmp_path / "nope.json")
    assert s == Settings() and err is None


def test_partial_file_fills_defaults(tmp_path):
    p = tmp_path / "s.json"
    p.write_text('{"pinned": "a"}')
    s, err = Settings.load(p)
    assert err is None and s.pinned == "a" and s.rotation_interval_s == 30


@pytest.mark.parametrize("raw", [
    "{not json", "[]", "null", '{"rotation": "a"}', '{"pinned": 5}', '{"schema": 2}', '{"bogus": 1}',
    '{"ghost_every": -1}', '{"ghost_clear_minutes": 121}', b"\xff\xfe\x00bad utf8",
])
def test_corrupt_file_gives_defaults_error_and_keeps_the_bad_file(tmp_path, raw):
    p = tmp_path / "s.json"
    p.write_bytes(raw if isinstance(raw, bytes) else raw.encode())
    s, err = Settings.load(p)
    assert s == Settings()
    assert err and "using defaults" in err and str(p) in err
    assert not p.exists() and (tmp_path / "s.json.corrupt").exists()
    s.save(p)  # saving afterwards cannot destroy the user's file
    assert (tmp_path / "s.json.corrupt").read_bytes() == (raw if isinstance(raw, bytes) else raw.encode())


def test_unreadable_path_is_defaults_plus_error(tmp_path):
    s, err = Settings.load(tmp_path)  # a directory
    assert s == Settings() and "cannot read" in err


def test_corrupt_rename_failure_is_tolerated(tmp_path, monkeypatch):
    p = tmp_path / "s.json"
    p.write_text("{")
    monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(OSError("ro fs")))
    s, err = Settings.load(p)
    assert s == Settings() and err


@pytest.mark.parametrize("bad, key", [
    ({"rotation": ["A"]}, "rotation"), ({"rotation": ["a", "a"]}, "rotation"), ({"rotation": [1]}, "rotation"),
    ({"rotation": [""]}, "rotation"), ({"rotation": ["x" * 65]}, "rotation"),
    ({"rotation_interval_s": 4}, "rotation_interval_s"), ({"rotation_interval_s": 86401}, "rotation_interval_s"),
    ({"rotation_interval_s": 5.5}, "rotation_interval_s"), ({"rotation_interval_s": True}, "rotation_interval_s"),
    ({"idle_timeout_s": 4}, "idle_timeout_s"), ({"idle_timeout_s": "60"}, "idle_timeout_s"),
    ({"ghost_every": 10001}, "ghost_every"), ({"ghost_clear_minutes": 121}, "ghost_clear_minutes"),
    ({"ghost_clear_minutes": -1}, "ghost_clear_minutes"), ({"ghost_clear_minutes": 2.5}, "ghost_clear_minutes"),
    ({"ghost_clear_minutes": True}, "ghost_clear_minutes"), ({"ghost_clear_minutes": "10"}, "ghost_clear_minutes"),
    ({"ghost_clear_minutes": 121}, "ghost_clear_minutes"), ({"ghost_clear_minutes": -1}, "ghost_clear_minutes"),
    ({"ghost_clear_minutes": 2.5}, "ghost_clear_minutes"), ({"ghost_clear_minutes": True}, "ghost_clear_minutes"),
    ({"ghost_clear_minutes": "10"}, "ghost_clear_minutes"), ({"pinned": "Bad Id"}, "pinned"),
    ({"auto_now_playing": 1}, "auto_now_playing"), ({"now_playing_screen": ""}, "now_playing_screen"),
    ({"screen_settings": []}, "screen_settings"), ({"screen_settings": {"a": 3}}, "screen_settings.a"),
    ({"screen_settings": {"A!": {}}}, "screen_settings.A!"), ({"nope": 1}, "nope"),
])
def test_validate_rejects(bad, key):
    errs = Settings.validate(bad)
    assert any(e.startswith(key) for e in errs), errs
    with pytest.raises(ValueError):
        Settings.from_dict(bad)


def test_validate_reports_every_problem():
    errs = Settings.validate({"rotation": "x", "idle_timeout_s": 0, "zzz": 1})
    assert len(errs) == 3


def test_validate_boundaries_accepted():
    ok = {"rotation": [], "rotation_interval_s": 5, "idle_timeout_s": 3600, "ghost_clear_minutes": 120, "pinned": None,
          "screen_settings": {"a": {}}}
    assert Settings.validate(ok) == []


def test_ghost_clear_minutes_boundaries_accepted():
    assert Settings.validate({"ghost_clear_minutes": 0}) == [] and Settings.validate({"ghost_clear_minutes": 120}) == []
