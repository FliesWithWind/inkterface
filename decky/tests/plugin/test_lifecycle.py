"""Plugin lifecycle, events, logging and uninstall (main.py against a fake decky module)."""
import asyncio
import json
import logging
from pathlib import Path

import pytest

from inkterface_core import proto as P
from inkterface_core import plugin_core
from inkterface_core.emulator import EmulatedPanel

PLUGIN_VERSION = json.loads((Path(__file__).resolve().parents[2] / "package.json").read_text())["version"]


def test_main_starts_everything_and_reports_connected(h):
    async def body(p):
        st = await p.get_status()
        assert st["ui_state"] == "connected" and st["connected"] and st["fw_version"] == "emu-fw"
        assert st["plugin_version"] == PLUGIN_VERSION and st["idle_supported"] is False
        assert {s["id"] for s in await p.list_screens()} >= {"telemetry", "big-clock", "now-playing", "thermals-power"}
        assert h.registries[0].cfg["units"] == "metric"
        assert (h.settings_dir / "screens").is_dir()
    h.run(body)


def test_status_event_is_emitted_when_the_panel_connects(h):
    async def body(p):
        await h.until(lambda: any(a[0]["ui_state"] == "connected" for e, a in h.events if e == "status"))
    h.run(body, wait_connected=False)


def test_unload_stops_threads_and_hands_the_panel_back(h):
    async def go():
        async with h.started() as p:
            await h.until(lambda: p.core.engine.status()["connected"])
            link = h.links[0]
            assert h.leaked_threads
        assert h.leaked_threads == []
        assert link.closed and link.written[-1].type == P.T_MODE and link.written[-1].payload[0] == P.MODE_PANEL
        assert h.registries[0].stopped == 1
        assert p.core is None and p._watch is None
        with pytest.raises(RuntimeError, match="not running"):
            await p.get_status()
    asyncio.run(go())


def test_unload_twice_and_unload_without_main_are_harmless(h):
    async def go():
        p = h.mod.Plugin()
        await p._unload()
        async with h.started() as q:
            pass
        await q._unload()
    asyncio.run(go())


def test_callables_before_main_raise_a_clear_error(h):
    async def go():
        p = h.mod.Plugin()
        for call in (p.get_status(), p.list_screens(), p.get_settings(), p.set_settings({"units": "metric"}),
                     p.pin_screen("a"), p.unpin_screen(), p.set_rotation(["a"]), p.set_now_playing(1),
                     p.import_package("x"), p.approve_package("a", "1.0.0", "x"), p.uninstall_package("a"),
                     p.preview_screen("a"), p.preview_idle_screen("a"), p.preview_idle_screen("a", "mono"), p.push_idle_layouts(), p.set_screen_enabled("a", True),
                     p.set_screen_settings("a", {})):
            with pytest.raises(RuntimeError, match="not running"):
                await call
    asyncio.run(go())


def test_main_failure_leaks_nothing(h):
    def boom(settings):
        raise OSError("sources broke")
    h.mod.registry_factory = boom

    async def go():
        with pytest.raises(OSError, match="sources broke"):
            await h.mod.Plugin()._main()
    asyncio.run(go())
    assert h.leaked_threads == []


def test_engine_failure_after_sources_started_stops_the_sources(h, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("engine broke")
    monkeypatch.setattr(plugin_core, "Engine", boom)

    async def go():
        with pytest.raises(RuntimeError, match="engine broke"):
            await h.mod.Plugin()._main()
    asyncio.run(go())
    assert h.leaked_threads == [] and h.registries[0].stopped == 1


def test_missing_panel_is_waiting_and_everything_else_still_works(h):
    h.plugged = False

    async def body(p):
        await h.until(lambda: p.core.engine.status()["last_error"])
        st = await p.get_status()
        assert st["ui_state"] == "waiting" and not st["connected"] and "no panel found" in st["last_error"]
        assert (await p.set_settings({"units": "imperial"}))["ok"]
        assert (await p.set_now_playing(570))["ok"] and (await p.get_status())["now_playing"] == 570
        r = await p.push_idle_layouts()
        assert not r["ok"] and r["error"] == "panel not connected"
        assert (await p.preview_screen("big-clock"))["ok"]
        h.plugged = True
        await h.until(lambda: p.core.engine.status()["connected"], timeout=15)   # backoff is 2 s
    h.run(body, wait_connected=False)


def test_old_firmware_is_reported_as_too_old(h):
    class OldPanel(EmulatedPanel):
        def _send(self, t, payload=b""):   # answer HELLO as a protocol-2 panel
            if t == P.T_HELLO:
                x = P.parse_hello(payload)
                payload = P.hello_payload(P.Hello(2, x.max_payload, x.rx_window, x.width, x.height, x.caps,
                                                  x.idle_flash_free, x.fw_ver))
            return super()._send(t, payload)

    h.panel = OldPanel(fw_ver="old-fw")

    async def body(p):
        await h.until(lambda: p.core.engine.status()["state"] == "refused")
        st = await p.get_status()
        assert st["ui_state"] == "firmware_too_old" and "update the firmware" in st["last_error"]
        assert st["fw_version"] == "old-fw" and not st["connected"] and not st["idle_supported"]
    h.run(body, wait_connected=False)


def test_core_logs_go_to_the_decky_logger_and_are_detached_on_unload(h):
    records = []

    class Cap(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    cap = Cap()
    h.logger.addHandler(cap)
    core_log = logging.getLogger("inkterface_core")

    async def go():
        async with h.started() as p:
            await h.until(lambda: p.core.engine.status()["connected"])
            await p.set_settings({"units": "imperial"})
            assert cap in core_log.handlers
        assert cap not in core_log.handlers
    asyncio.run(go())
    h.logger.removeHandler(cap)
    assert any("settings changed: units" in r for r in records) and "Inkterface loaded" in records


def test_watcher_survives_a_failing_poll_and_stops_cleanly(h, monkeypatch):
    async def body(p):
        real = p.core.get_status
        calls = []

        def flaky():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("poll failed")
            return real()
        monkeypatch.setattr(p.core, "get_status", flaky)
        await h.until(lambda: len(calls) >= 3)
        assert any("status watcher" in r.getMessage() for r in h.caplog.records)
    h.run(body)


def test_mutations_emit_events_only_on_success(h):
    async def body(p):
        h.events.clear()
        assert not (await p.set_settings({"units": "bogus"}))["ok"]
        assert h.names("settings_changed", "screens_changed") == []
        assert (await p.set_settings({"units": "imperial"}))["ok"]
        assert h.names("settings_changed") == ["settings_changed"]
        assert (await p.pin_screen("big-clock"))["ok"] and (await p.unpin_screen())["ok"]
        assert (await p.set_rotation(["big-clock"], 20))["ok"]
        assert (await p.set_screen_enabled("big-clock", False))["ok"]
        assert "screens_changed" in h.names()
        assert h.events[-1][0] == "status"
    h.run(body)


def test_uninstall_keeps_user_data_by_default(h):
    async def body(p):
        await p.set_settings({"steamid64": "76561198000000000", "rotation_interval_s": 15})
    h.run(body)
    asyncio.run(h.mod.Plugin()._uninstall())
    assert (h.settings_dir / "settings.json").exists() and (h.settings_dir / "host.json").exists()
    assert (h.settings_dir / "screens").is_dir()


def test_uninstall_purges_when_asked(h):
    async def body(p):
        assert (await p.set_settings({"purge_on_uninstall": True, "rotation_interval_s": 15}))["ok"]
    h.run(body)
    asyncio.run(h.mod.Plugin()._uninstall())
    assert not (h.settings_dir / "settings.json").exists() and not (h.settings_dir / "host.json").exists()
    assert not (h.settings_dir / "screens").exists()


def test_purge_ignores_a_corrupt_host_file(tmp_path):
    (tmp_path / "host.json").write_text("{nope")
    (tmp_path / "settings.json").write_text("{}")
    assert plugin_core.purge_if_requested(tmp_path) is False
    assert (tmp_path / "settings.json").exists()


def test_version_falls_back_when_package_json_is_missing(h):
    (h.plugin_dir / "package.json").unlink()

    async def body(p):
        assert (await p.get_status())["plugin_version"] == "unknown"
    h.run(body)


def test_settings_survive_a_restart(h):
    async def first(p):
        assert (await p.set_rotation(["big-clock", "telemetry"], 45))["ok"]
        assert (await p.pin_screen("telemetry"))["ok"]
        assert (await p.set_settings({"weather_lat": 52.5, "weather_lon": 13.4, "units": "imperial"}))["ok"]
    h.run(first)

    async def second(p):
        s = await p.get_settings()
        assert s["rotation"] == ["big-clock", "telemetry"] and s["rotation_interval_s"] == 45 and s["pinned"] == "telemetry"
        assert (s["weather_lat"], s["weather_lon"], s["units"]) == (52.5, 13.4, "imperial")
        assert h.registries[-1].cfg["weather_lat"] == 52.5
    h.run(second)


def test_corrupt_settings_files_fall_back_to_defaults_and_say_so(h):
    h.settings_dir.mkdir()
    (h.settings_dir / "settings.json").write_text("{broken")
    (h.settings_dir / "host.json").write_text('{"steam_api_key": 5}')

    async def body(p):
        s = await p.get_settings()
        assert s["rotation_interval_s"] == 30 and s["steam_api_key"] == "unset"
        assert len(s["errors"]) == 2
        assert (await p.get_status())["host_error"]
    h.run(body)
    assert (h.settings_dir / "settings.json.corrupt").exists() and (h.settings_dir / "host.json.corrupt").exists()
