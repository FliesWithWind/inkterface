"""Settings, pin, rotation, enable, now-playing, preview and idle callables: happy path + invalid input."""
import base64
import io
import json

import pytest
from PIL import Image
from packages_testlib import package_zip, screen_json

from inkterface_core import plugin_core
from inkterface_core.emulator import EmulatedPanel
from inkterface_core.engine import Settings

BAD = [None, 5, True, [], {}, 1.5]


def idle_only_zip(pid="idle-demo"):
    doc = {"schema": 1, "id": pid, "name": "Idle demo", "version": "1.0.0", "author": "T",
           "idle": {"render_mode": "mono", "widgets": [{"type": "text", "x": 0, "y": 0, "w": 100, "h": 20}]}}
    return package_zip(pid, doc=doc)


def settings_zip(pid="cfg-demo"):
    doc = screen_json(pid, settings=[
        {"key": "n", "type": "number", "default": 3, "min": 1, "max": 9, "label": "N"},
        {"key": "mode", "type": "enum", "default": "a", "values": ["a", "b"]},
        {"key": "on", "type": "bool", "default": False}])
    return package_zip(pid, doc=doc)


# -- status / lists ------------------------------------------------------------------
def test_status_and_screen_list_shapes(h):
    async def body(p):
        st = await p.get_status()
        for k in ("ui_state", "connected", "state", "fw_version", "current_screen", "pinned", "now_playing", "last_error",
                  "idle_supported", "plugin_version", "frames_rendered", "protocol_version", "panel_size"):
            assert k in st, k
        json.dumps(st)
        await h.until(lambda: p.core.engine.status()["current_screen"])
        screens = {s["id"]: s for s in await p.list_screens()}
        t = screens["telemetry"]
        assert t["builtin"] and t["enabled"] and t["has_active"] and not t["needs_approval"] and not t["has_code"]
        assert t["refresh_s"] and t["settings_schema"] is not None and t["in_rotation"]
        assert sum(s["current"] for s in screens.values()) == 1
        json.dumps(screens)
    h.run(body)


def test_get_settings_defaults_have_no_secret(h):
    async def body(p):
        s = await p.get_settings()
        assert s["steam_api_key"] == "unset" and s["units"] == "metric" and s["weather_lat"] is None
        assert s["rotation"] == ["telemetry", "thermals-power", "big-clock"] and s["errors"] == []
        assert s["idle_screens"] == [] and s["purge_on_uninstall"] is False and s["auto_now_playing"] is True
    h.run(body)


# -- set_settings --------------------------------------------------------------------
def test_set_settings_engine_keys_apply_and_persist(h):
    async def body(p):
        r = await p.set_settings({"rotation_interval_s": 10, "idle_timeout_s": 120, "ghost_clear_minutes": 5,
                                  "auto_now_playing": False, "now_playing_screen": "big-clock"})
        assert r["ok"] and r["settings"]["rotation_interval_s"] == 10
        e = p.core.engine.settings
        assert (e.rotation_interval_s, e.idle_timeout_s, e.ghost_clear_minutes, e.auto_now_playing, e.now_playing_screen) == (
            10, 120, 5, False, "big-clock")
        assert json.loads((h.settings_dir / "settings.json").read_text())["rotation_interval_s"] == 10
    h.run(body)


@pytest.mark.parametrize("patch", [
    {"rotation_interval_s": 1},
    {"idle_timeout_s": 99999}, {"ghost_clear_minutes": -1}, {"ghost_clear_minutes": 121}, {"ghost_clear_minutes": 1.5},
    {"ghost_clear_minutes": "10"}, {"ghost_every": 30},
    {"now_playing_screen": "nope"}, {"now_playing_screen": "../x"},
    {"units": "kelvin"}, {"weather_lat": 91, "weather_lon": 0}, {"weather_lat": 10},
    {"weather_lon": 181, "weather_lat": 0}, {"weather_lat": "1", "weather_lon": "2"}, {"weather_lat": True, "weather_lon": 1},
    {"weather_lat": float("nan"), "weather_lon": 1},
    {"steamid64": "123"}, {"steamid64": 76561198000000000}, {"steam_api_key": "short"}, {"steam_api_key": 5},
    {"idle_screens": "x"}, {"idle_screens": ["telemetry"]}, {"idle_screens": ["nope"]}, {"purge_on_uninstall": 1},
    {"rotation": ["telemetry"]}, {"bogus": 1},
    {}, None, "units",
])
def test_set_settings_rejects_bad_input_and_changes_nothing(h, patch):
    async def body(p):
        before = (await p.get_settings(), len(h.registries))
        r = await p.set_settings({"ghost_clear_minutes": 7, **patch} if isinstance(patch, dict) and patch else patch)
        assert r["ok"] is False and r["error"]
        assert (await p.get_settings(), len(h.registries)) == before   # all-or-nothing
        assert not (h.settings_dir / "host.json").exists()
    h.run(body)


def test_host_settings_rebuild_the_registry_only_when_sources_change(h):
    async def body(p):
        first = h.registries[0]
        r = await p.set_settings({"weather_lat": 48.1, "weather_lon": 11.6, "units": "imperial",
                                  "steamid64": "76561198000000000"})
        assert r["ok"] and len(h.registries) == 2 and first.stopped == 1
        assert p.core.engine.registry is h.registries[1]
        assert h.registries[1].cfg == {"weather_lat": 48.1, "weather_lon": 11.6, "units": "imperial",
                                       "steamid64": "76561198000000000", "steam_api_key": None}
        assert (await p.set_settings({"units": "imperial"}))["ok"] and len(h.registries) == 2   # unchanged
        assert (await p.set_settings({"weather_lat": None, "weather_lon": None}))["ok"] and len(h.registries) == 3
        assert (await p.set_settings({"purge_on_uninstall": True}))["ok"] and len(h.registries) == 3
        assert h.host_file()["purge_on_uninstall"] is True
    h.run(body)


def test_now_playing_survives_a_registry_rebuild(h):
    async def body(p):
        await p.set_now_playing(570)
        await p.set_settings({"units": "imperial"})
        assert h.registries[1].np == [570]
    h.run(body)


def test_registry_factory_failure_is_an_error_not_a_crash(h):
    async def body(p):
        def boom(s):
            raise ValueError("bad source config")
        h.mod.registry_factory = boom
        r = await p.set_settings({"units": "imperial"})
        assert not r["ok"] and "bad source config" in r["error"]
        assert (await p.get_settings())["units"] == "metric" and not (h.settings_dir / "host.json").exists()
    h.run(body)


def test_disk_failures_are_reported(h, monkeypatch):
    async def body(p):
        monkeypatch.setattr(plugin_core.HostConfig, "save", lambda self, path: (_ for _ in ()).throw(OSError(28, "No space left on device")))
        r = await p.set_settings({"units": "imperial"})
        assert not r["ok"] and "No space left" in r["error"] and (await p.get_settings())["units"] == "metric"
        monkeypatch.undo()
        monkeypatch.setattr(Settings, "save", lambda self, path: (_ for _ in ()).throw(OSError(30, "Read-only file system")))
        for r in (await p.set_settings({"rotation_interval_s": 9}), await p.pin_screen("big-clock"),
                  await p.set_rotation(["big-clock"])):
            assert not r["ok"] and "Read-only" in r["error"]
        assert p.core.engine.settings.pinned is None and p.core.engine.settings.rotation_interval_s == 30
    h.run(body)


# -- pin / rotation ------------------------------------------------------------------
def test_pin_and_unpin_drive_the_panel(h):
    async def body(p):
        assert (await p.pin_screen("big-clock"))["ok"]
        await h.until(lambda: p.core.engine.status()["current_screen"] == "big-clock")
        assert (await p.get_status())["pinned"] == "big-clock"
        assert [s["id"] for s in await p.list_screens() if s["pinned"]] == ["big-clock"]
        assert (await p.unpin_screen())["ok"] and (await p.unpin_screen())["ok"]   # idempotent
        assert (await p.get_status())["pinned"] is None
    h.run(body)


@pytest.mark.parametrize("sid", ["nope", None, 5, "../etc"])
def test_pin_rejects_unknown_screens(h, sid):
    async def body(p):
        r = await p.pin_screen(sid)
        assert r["ok"] is False and p.core.engine.settings.pinned is None
    h.run(body)


def test_set_rotation(h):
    async def body(p):
        assert (await p.set_rotation(["big-clock", "telemetry"], 20))["ok"]
        s = await p.get_settings()
        assert s["rotation"] == ["big-clock", "telemetry"] and s["rotation_interval_s"] == 20
        assert (await p.set_rotation(["telemetry"]))["ok"] and (await p.get_settings())["rotation_interval_s"] == 20
    h.run(body)


@pytest.mark.parametrize("ids,interval", [([], None), ("telemetry", None), (None, None), (["nope"], None),
                                          (["telemetry", "telemetry"], None), (["telemetry"], 1), (["telemetry"], "5"),
                                          (["telemetry"], True), ([5], None)])
def test_set_rotation_rejects_bad_input(h, ids, interval):
    async def body(p):
        before = await p.get_settings()
        r = await p.set_rotation(ids, interval)
        assert r["ok"] is False and r["error"] and await p.get_settings() == before
    h.run(body)


def test_a_stale_rotation_id_does_not_block_other_edits(h):
    h.settings_dir.mkdir()
    (h.settings_dir / "settings.json").write_text(json.dumps({"schema": 1, "rotation": ["telemetry", "removed-pkg"]}))

    async def body(p):
        assert (await p.set_settings({"rotation_interval_s": 15}))["ok"]
        assert (await p.get_settings())["rotation"] == ["telemetry", "removed-pkg"]
    h.run(body)


# -- enable / disable -------------------------------------------------------------------
def test_disable_and_enable_builtin_and_pinned_screen_is_unpinned(h):
    async def body(p):
        await p.pin_screen("big-clock")
        assert (await p.set_screen_enabled("big-clock", False))["ok"]
        assert "big-clock" not in p.core.engine.screens and p.core.engine.settings.pinned is None
        assert not [s for s in await p.list_screens() if s["id"] == "big-clock"][0]["enabled"]
        assert (await p.pin_screen("big-clock"))["ok"] is False
        assert (await p.set_screen_enabled("big-clock", True))["ok"] and "big-clock" in p.core.engine.screens
    h.run(body)


@pytest.mark.parametrize("sid,enabled", [("nope", True), ("telemetry", "yes"), ("telemetry", 1), (5, False)])
def test_enable_rejects_bad_input(h, sid, enabled):
    async def body(p):
        assert (await p.set_screen_enabled(sid, enabled))["ok"] is False
    h.run(body)


def test_disabling_every_screen_is_reported_by_the_engine_not_a_crash(h):
    async def body(p):
        for s in ("telemetry", "thermals-power", "big-clock", "now-playing"):
            assert (await p.set_screen_enabled(s, False))["ok"]
        await h.until(lambda: p.core.engine.status()["last_error"] == "no active screens available")
        assert (await p.get_status())["connected"]
    h.run(body)


# -- per-screen settings ---------------------------------------------------------------------
def test_screen_settings_are_validated_stored_and_listed(h):
    async def body(p):
        assert (await p.import_package(h.package("c.zip", settings_zip())))["ok"]
        r = await p.set_screen_settings("cfg-demo", {"n": 5, "mode": "b"})
        assert r["ok"] and r["settings"] == {"n": 5, "mode": "b", "on": False}
        s = [x for x in await p.list_screens() if x["id"] == "cfg-demo"][0]
        assert s["settings"] == {"n": 5, "mode": "b", "on": False} and len(s["settings_schema"]) == 3
        assert (await p.get_settings())["screen_settings"] == {"cfg-demo": {"n": 5, "mode": "b"}}
        assert (await p.set_screen_settings("cfg-demo", {}))["ok"]
        assert (await p.get_settings())["screen_settings"] == {}
    h.run(body)


@pytest.mark.parametrize("sid,values,msg", [
    ("cfg-demo", {"n": 99}, "<="), ("cfg-demo", {"n": 0}, ">="), ("cfg-demo", {"n": "3"}, "number"),
    ("cfg-demo", {"n": True}, "number"), ("cfg-demo", {"mode": "z"}, "one of"), ("cfg-demo", {"on": 1}, "bool"),
    ("cfg-demo", {"nope": 1}, "unknown setting"), ("cfg-demo", None, "object"),
    ("missing", {}, "not installed"), (None, {}, "string"),
])
def test_screen_settings_reject_bad_input(h, sid, values, msg):
    async def body(p):
        await p.import_package(h.package("c.zip", settings_zip()))
        r = await p.set_screen_settings(sid, values)
        assert r["ok"] is False and msg in r["error"]
        assert (await p.get_settings())["screen_settings"] == {}
    h.run(body)


def test_screen_settings_change_the_render(h):
    async def body(p):
        a = png((await p.preview_screen("big-clock"))["data_url"]).tobytes()
        assert (await p.set_screen_settings("big-clock", {"time_format": "XXXXX", "date_format": "YYYYY"}))["ok"]
        b = png((await p.preview_screen("big-clock"))["data_url"]).tobytes()
        assert a != b
    h.run(body)


# -- now playing -----------------------------------------------------------------------------
def test_now_playing_switches_the_screen_and_exit_returns(h):
    async def body(p):
        assert (await p.set_now_playing(570))["ok"]
        await h.until(lambda: p.core.engine.status()["current_screen"] == "now-playing")
        assert (await p.get_status())["now_playing"] == 570 and h.registries[0].np == [570]
        assert (await p.set_now_playing("730"))["ok"] and (await p.get_status())["now_playing"] == 730
        assert (await p.set_now_playing(None))["ok"]
        await h.until(lambda: p.core.engine.status()["current_screen"] != "now-playing")
        assert (await p.get_status())["now_playing"] is None
    h.run(body)


# plumbing only (error shape, state untouched); the whole input space is in sources/test_src_steam_local.py::test_invalid_appid_rejected
@pytest.mark.parametrize("appid", ["abc", -1, True])
def test_now_playing_rejects_bad_appids(h, appid):
    async def body(p):
        r = await p.set_now_playing(appid)
        assert r["ok"] is False and "appid" in r["error"]
        assert (await p.get_status())["now_playing"] is None and h.registries[0].np == []
    h.run(body)


# -- preview ---------------------------------------------------------------------------------
def png(url):
    assert url.startswith("data:image/png;base64,")
    return Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1])))


def test_preview_renders_a_648x480_one_bit_png_with_live_data(h):
    async def body(p):
        for sid in ("telemetry", "big-clock", "now-playing", "thermals-power"):
            r = await p.preview_screen(sid)
            assert r["ok"], r
            im = png(r["data_url"])
            assert im.size == (648, 480) and im.mode == "1"
        assert png((await p.preview_screen("telemetry"))["data_url"]).tobytes() != \
            png((await p.preview_screen("big-clock"))["data_url"]).tobytes()
    h.run(body)


def test_preview_works_for_a_disabled_declarative_screen(h):
    async def body(p):
        await p.set_screen_enabled("big-clock", False)
        assert (await p.preview_screen("big-clock"))["ok"]
    h.run(body)


@pytest.mark.parametrize("sid", ["nope", None])
def test_preview_rejects_unknown_screens(h, sid):
    async def body(p):
        assert (await p.preview_screen(sid))["ok"] is False
    h.run(body)


def test_preview_of_an_idle_only_screen_is_an_error(h):
    async def body(p):
        await p.import_package(h.package("i.zip", idle_only_zip()))
        r = await p.preview_screen("idle-demo")
        assert r["ok"] is False and "active" in r["error"]
    h.run(body)


def test_preview_with_stored_settings_that_no_longer_validate_is_an_error(h):
    h.settings_dir.mkdir()
    (h.settings_dir / "settings.json").write_text(json.dumps({"schema": 1, "screen_settings": {"big-clock": {"zzz": 1}}}))

    async def body(p):
        r = await p.preview_screen("big-clock")
        assert r["ok"] is False and "unknown setting" in r["error"]
        s = [x for x in await p.list_screens() if x["id"] == "big-clock"][0]
        assert s["error"] and s["settings"]    # the list still works and shows defaults
    h.run(body)


# -- idle ------------------------------------------------------------------------------------
def test_idle_screens_selection(h):
    async def body(p):
        await p.import_package(h.package("i.zip", idle_only_zip()))
        assert (await p.set_settings({"idle_screens": ["idle-demo"]}))["ok"]
        assert [s["id"] for s in await p.list_screens() if s["in_idle"]] == ["idle-demo"]
        assert [s for s in await p.list_screens() if s["id"] == "idle-demo"][0]["has_idle"]
        assert h.host_file()["idle_screens"] == ["idle-demo"]
    h.run(body)


def test_idle_push_says_not_supported_without_the_capability(h):
    async def body(p):
        r = await p.push_idle_layouts()
        assert r == {"ok": False, "error": "this firmware has no idle storage: update the firmware", "supported": False}
        assert (await p.get_status())["idle_supported"] is False
    h.run(body)
