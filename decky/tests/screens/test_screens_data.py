import math

import pytest

from inkterface_core.screens import data as D
from inkterface_core.screens.data import DataSnapshot


def test_nested_flat_and_mixed_lookup():
    s = DataSnapshot({
        "weather": {"temp": 3, "forecast": [{"high": 9}, {"high": 8}]},
        "system.cpu.load": 40, "system.cpu.load.history": [1, 2],
        "steam.now_playing": {"name": "Game"},
    })
    assert s.get("weather.temp") == 3
    assert s.get("weather.forecast[1].high") == 8
    assert s.get("system.cpu.load") == 40
    assert s.get("system.cpu.load.history") == [1, 2]
    assert s.get("steam.now_playing.name") == "Game"
    assert len(s) == 4 and set(iter(s)) == {"weather", "system.cpu.load", "system.cpu.load.history", "steam.now_playing"}


@pytest.mark.parametrize("path", ["nope", "weather.nope", "weather.temp.deeper", "weather.forecast[5].high",
                                  "weather[0]", "steam.now_playing.name.x"])
def test_missing_paths_give_none(path):
    s = DataSnapshot({"weather": {"temp": 3, "forecast": []}, "steam.now_playing": {"name": "g"}})
    assert s.get(path) is None
    with pytest.raises(KeyError):
        s[path]


def test_null_parent_is_missing():
    assert DataSnapshot({"steam.now_playing": None}).get("steam.now_playing.name") is None


def test_walk_backtracks_between_flat_prefixes():
    s = DataSnapshot({"a": {"x": 1}, "a.b": {"c": 2}})
    assert s.get("a.b.c") == 2 and s.get("a.x") == 1
    s = DataSnapshot({"a.b": {"z": 1}, "a": {"b": {"c": 2}}})    # the longer key dead-ends, the shorter one works
    assert s.get("a.b.c") == 2 and s.get("a.b.z") == 1


def test_default_empty():
    assert DataSnapshot().get("x") is None and len(DataSnapshot(None)) == 0


def test_typed_accessors():
    s = DataSnapshot({"n": 1.5, "b": True, "s": "x", "inf": math.inf, "nan": math.nan, "ser": [1, "a", None, 2.5, True, math.nan]})
    assert s.number("n") == 1.5
    assert s.number("b") is None and s.number("s") is None and s.number("inf") is None and s.number("nan") is None
    assert s.string("s") == "x" and s.string("n") is None
    assert s.series("ser") == [1, 2.5]
    assert s.series("n") is None and s.series("missing") is None
    assert s.value("s") == "x"


def test_time_parsing():
    s = DataSnapshot({"e": 0, "iso": "2026-11-10T14:32:00+01:00", "naive": "2026-11-10T14:32:00",
                      "bad": "yesterday", "huge": 1e30, "t": [1]})
    assert s.time("e").year == 1970 and s.time("e").utcoffset().total_seconds() == 0
    assert s.time("iso").utcoffset().total_seconds() == 3600
    assert s.time("naive").tzinfo is None
    assert s.time("bad") is None and s.time("huge") is None and s.time("t") is None and s.time("missing") is None


def test_time_accepts_datetime_objects_as_emitted_by_the_time_source():
    from datetime import datetime, timezone
    now = datetime(2026, 11, 10, 14, 32, tzinfo=timezone.utc)
    assert DataSnapshot({"t": now}).time("t") == now


def test_with_settings_does_not_mutate():
    s = DataSnapshot({"a": 1}, base_dir="/x")
    t = s.with_settings({"k": 2})
    assert t.get("settings.k") == 2 and s.get("settings.k") is None and t.base_dir == "/x" and t.get("a") == 1


def test_source_kind_registry():
    assert D.source_kind("system.cpu.load") == "number"
    assert D.source_kind("system.cpu.load.history") == "series"
    assert D.source_kind("steam.now_playing.box_art_path") == "path"
    assert D.source_kind("weather.forecast[2].high") == "number"
    assert D.source_kind("steam.recent[0].name") == "string"
    assert D.source_kind("time.now") == "time"
    assert D.source_kind("cpu.tmp") is None
    assert D.source_kind("settings.a", {"a": "bool"}) == "bool"
    assert D.source_kind("settings.b", {"a": "bool"}) is None
    assert D.source_kind("settings.a") is None


@pytest.mark.parametrize("path", [f"system.{p}" for p in (
    "cpu.load", "cpu.temp", "cpu.clock", "gpu.load", "gpu.temp", "gpu.clock", "gpu.mem_clock",
    "mem.used_pct", "fan.rpm", "power.package_w")])
def test_contract_metrics_have_history(path):
    assert D.SOURCES[path] == "number" and D.SOURCES[path + ".history"] == "series"


def test_parse_template():
    assert D.parse_template("a {x.y:.1f} b {{c}}") == ["a ", ("x.y", ".1f"), " b ", "{", "c", "}"]
    assert D.parse_template("{w.forecast[0].high}") == [("w.forecast[0].high", "")]
    assert D.parse_template("") == []
    for bad in ("{", "}", "a { b", "{x", "{a b}", "{}"):
        with pytest.raises(ValueError):
            D.parse_template(bad)


def test_render_template_values_and_placeholders():
    s = DataSnapshot({"a": 12.0, "b": 3.14159, "c": True, "d": "txt", "e": 7, "inf": math.inf, "lst": [1]})
    r = lambda t: D.render_template(t, s)
    assert r("{a}|{b}|{c}|{d}|{e}") == "12|3.14159|yes|txt|7"
    assert r("{a:.1f}%") == "12.0%"
    assert r("{missing}") == "--"
    assert r("{inf}") == "--" and r("{lst}") == "--"
    assert r("{d:d}") == "--"            # spec does not fit the value
    assert r("{{literal}}") == "{literal}"


def test_format_value_none_and_collections():
    assert D.format_value(None) is None and D.format_value({"a": 1}) is None and D.format_value([1]) is None
    assert D.format_value(False) == "no"


def test_snapshot_raw_is_the_underlying_mapping():
    raw = {"a": {"b": 1}}
    assert D.DataSnapshot(raw).raw is raw and D.DataSnapshot().raw == {}
