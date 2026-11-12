"""Idle layouts end to end: plugin -> engine -> USB frames -> emulated panel (decky/tests/plugin/conftest.py harness).

Covers: upload on connect and on change, a panel without idle storage, refusal before any byte is sent, an upload NAK,
the idle timeout in MODE, and that no secret (Wi-Fi password, Steam key) reaches a log, status, event or return value.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "packages"))
# the `h` harness fixture lives in plugin/conftest.py (not importable as a package): load it by path and re-export
_spec = importlib.util.spec_from_file_location("plugin_harness", HERE / "plugin" / "conftest.py")
_harness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_harness)
Harness, h = _harness.Harness, _harness.h

from packages_testlib import package_zip  # noqa: E402

from inkterface_core import proto as P  # noqa: E402
from inkterface_core.emulator import EmulatedPanel  # noqa: E402
from inkterface_core.idle.store import parse_store  # noqa: E402

KEY = "ABCDEF0123456789ABCDEF0123456789"
WIFI_PASS = "hunter2-hunter2"
SECRETS = (KEY, WIFI_PASS)
CAPS = P.CAP_GRAY4 | P.CAP_IDLE_STORAGE


def panel(**kw):
    kw.setdefault("caps", CAPS)
    kw.setdefault("idle_free", 49152)
    return EmulatedPanel(fw_ver="emu-fw", **kw)


def host_json(h, **over):
    h.settings_dir.mkdir(parents=True, exist_ok=True)
    cfg = {"idle_screens": ["minimal-clock", "steam-stats"], "wifi_ssid": "HomeNet", "wifi_pass": WIFI_PASS,
           "steam_api_key": KEY, "steamid64": "76561198000000000", "weather_lat": 52.52, "weather_lon": 13.405,
           "units": "imperial", **over}
    (h.settings_dir / "host.json").write_text(json.dumps(cfg))


async def idle_state(p, want, timeout=10.0):
    await Harness.until(lambda: p.core.engine.status()["idle"]["state"] in want, timeout)
    return p.core.engine.status()["idle"]


def modes(h):
    return [m for m in h.panel.mode_frames]


def big_idle_zip(pid, n_widgets=56, text_len=100):
    """An idle layout of ~7 KB: eight of them overflow the 48 KiB store, one of 70 widgets overflows a layout."""
    ws = [{"type": "text", "x": 0, "y": (i % 20) * 20, "w": 600, "h": 20, "text": f"{pid}.{i:03d}".ljust(text_len, "x")}   # unique: the compiler pools equal strings
          for i in range(n_widgets)]
    doc = {"schema": 1, "id": pid, "name": pid, "version": "1.0.0", "author": "T",
           "idle": {"render_mode": "mono", "widgets": ws}}
    return package_zip(pid, doc=doc)


# -- upload on connect ----------------------------------------------------------------------------------------------
def test_upload_on_connect_carries_screens_rotation_config_and_timeout(h):
    h.panel = panel()
    host_json(h)

    async def body(p):
        idle = await idle_state(p, {"ok"})
        assert idle["ids"] == idle["on_panel"] == ["minimal-clock", "steam-stats"] and idle["error"] is None
        assert h.panel.idle_commits == 1 and idle["bytes"] == len(h.panel.idle_store)
        store = parse_store(h.panel.idle_store)
        assert store["n_layouts"] == 2 and store["active"] == 0 and store["rotate"] and store["rotate_s"] == 60
        c = store["config"]
        assert c["wifi_ssid"] == "HomeNet" and c["wifi_pass"] == WIFI_PASS      # secrets go to the panel...
        assert c["steam_key"] == KEY and c["steam_id"] == "76561198000000000"
        assert c["units"] == 1 and c["lat"] == "52.52" and c["lon"] == "13.405" and c["tz"]
        assert len(c["tz"]) <= 63
        assert (await p.get_status())["idle"]["on_panel"] == ["minimal-clock", "steam-stats"]
        # MODE HOST carries the default idle timeout (60 s) before the store goes out
        assert modes(h)[0] == b"\x00\x3c\x00"
        assert h.panel.mode == P.MODE_HOST
    h.run(body)


def test_upload_progress_reaches_the_ui_as_status_events(h):
    h.panel = panel()
    host_json(h)

    async def body(p):
        await idle_state(p, {"ok"})
        await h.until(lambda: any(e == "status" and a[0]["idle"]["state"] == "ok" for e, a in h.events))
        h.events.clear()
        assert (await p.set_settings({"idle_screens": ["minimal-clock"]}))["ok"]
        await h.until(lambda: any(e == "status" and a[0]["idle"]["on_panel"] == ["minimal-clock"] for e, a in h.events))
    h.run(body)


def test_nothing_selected_uploads_nothing(h):
    h.panel = panel()

    async def body(p):
        await h.until(lambda: p.core.engine.status()["frames_rendered"] > 0)
        st = await p.get_status()
        assert st["idle"]["state"] == "none" and st["idle"]["ids"] == [] and h.panel.idle_frames == []
        r = await p.push_idle_layouts()
        assert r["ok"] is False and r["supported"] is True and "no idle screens" in r["error"]
    h.run(body)


def test_single_screen_does_not_rotate_and_rotate_off_stays_on_first(h):
    h.panel = panel()
    host_json(h, idle_screens=["steam-stats", "minimal-clock"], idle_rotate_s=0)

    async def body(p):
        await idle_state(p, {"ok"})
        s = parse_store(h.panel.idle_store)
        assert s["n_layouts"] == 2 and not s["rotate"] and s["active"] == 0
        assert (await p.get_status())["idle"]["ids"] == ["steam-stats", "minimal-clock"]   # order is the user's
    h.run(body)


# -- re-upload on change / reconnect ----------------------------------------------------------------------------------
def test_change_reuploads_but_an_irrelevant_change_does_not(h):
    h.panel = panel()
    host_json(h)

    async def body(p):
        await idle_state(p, {"ok"})
        assert h.panel.idle_commits == 1
        assert (await p.set_settings({"purge_on_uninstall": True}))["ok"]
        assert (await p.set_settings({"idle_screens": ["minimal-clock", "steam-stats"]}))["ok"]   # same bytes
        await h.until(lambda: p.core.engine.status()["frames_rendered"] > 1)
        assert h.panel.idle_commits == 1
        assert (await p.set_settings({"idle_screens": ["minimal-clock"]}))["ok"]
        await h.until(lambda: h.panel.idle_commits == 2)
        idle = await idle_state(p, {"ok"})
        assert idle["on_panel"] == ["minimal-clock"] and parse_store(h.panel.idle_store)["n_layouts"] == 1
        assert (await p.set_settings({"wifi_ssid": "Other"}))["ok"]
        await h.until(lambda: h.panel.idle_commits == 3)
        assert parse_store(h.panel.idle_store)["config"]["wifi_ssid"] == "Other"
        assert (await p.set_settings({"units": "metric", "idle_rotate_s": 0}))["ok"]
        await h.until(lambda: h.panel.idle_commits == 4)
        assert parse_store(h.panel.idle_store)["config"]["units"] == 0
    h.run(body)


def test_every_new_connection_uploads_again(h):
    h.panel = panel()
    host_json(h)

    async def body(p):
        await idle_state(p, {"ok"})
        eng = p.core.engine
        eng.pause()
        eng.resume()
        await h.until(lambda: h.panel.idle_commits == 2, timeout=15)
        assert (await idle_state(p, {"ok"}))["on_panel"] == ["minimal-clock", "steam-stats"]
        assert len(h.links) == 2
    h.run(body)


def test_gray4_mode_is_chosen_per_screen_and_needs_the_capability(h):
    h.panel = panel()
    host_json(h, idle_screens=["clock-weather"], idle_modes={"clock-weather": "gray4"})

    async def body(p):
        await idle_state(p, {"ok"})
        lay = parse_store(h.panel.idle_store)["layouts"][0]
        assert lay[5] == 1   # ILY1 header: magic 4, version 1, then the render mode byte (1 = gray4)
        info = [s for s in await p.list_screens() if s["id"] == "clock-weather"][0]
        assert info["idle_mode"] == "gray4" and set(info["idle_modes"]) <= {"mono", "gray4"} and "gray4" in info["idle_modes"]
        assert (await p.set_settings({"idle_modes": {"clock-weather": "mono"}}))["ok"]
        await h.until(lambda: h.panel.idle_commits == 2)
        assert parse_store(h.panel.idle_store)["layouts"][0][5] == 0
    h.run(body)


def test_gray4_layout_on_a_panel_without_gray4_is_refused_before_upload(h):
    h.panel = panel(caps=P.CAP_IDLE_STORAGE)
    host_json(h, idle_screens=["clock-weather"], idle_modes={"clock-weather": "gray4"})

    async def body(p):
        idle = await idle_state(p, {"error"})
        assert "gray4" in idle["error"] and h.panel.idle_frames == []
    h.run(body)


def test_unsupported_mode_and_unknown_screen_are_refused(h):
    h.panel = panel()

    async def body(p):
        r = await p.set_settings({"idle_modes": {"nope": "mono"}})
        assert not r["ok"] and "nope" in r["error"]
        r = await p.set_settings({"idle_modes": {"minimal-clock": "sepia"}})
        assert not r["ok"] and "idle_modes" in r["error"]
        r = await p.set_settings({"idle_screens": ["telemetry"]})   # active-only screen: no idle layout
        assert not r["ok"] and "idle layout" in r["error"]
        for bad in ({"idle_rotate_s": 5}, {"idle_rotate_s": -1}, {"idle_rotate_s": True}, {"wifi_ssid": "x" * 33},
                    {"wifi_pass": "p" * 64}, {"wifi_ssid": "a\nb"}, {"wifi_pass": 5}):
            assert not (await p.set_settings(bad))["ok"], bad
        assert h.panel.idle_frames == []
        assert (await p.get_settings())["idle_screens"] == []
    h.run(body)


# -- capability ---------------------------------------------------------------------------------------------------
def test_panel_without_idle_storage_gets_a_clear_unsupported_status_and_a_bare_mode(h):
    h.panel = panel(caps=0, idle_free=0)
    host_json(h)

    async def body(p):
        await h.until(lambda: p.core.engine.status()["frames_rendered"] > 0)
        st = await p.get_status()
        assert st["idle"]["state"] == "unsupported" and "update the firmware" in st["idle"]["error"]
        assert st["idle_supported"] is False and st["connected"]
        r = await p.push_idle_layouts()
        assert r == {"ok": False, "error": "this firmware has no idle storage: update the firmware", "supported": False}
        assert h.panel.idle_frames == [] and h.panel.idle_store is None
        assert modes(h)[0] == b"\x00"   # an older firmware would NAK the extended MODE
        assert st["naks"] == 0
    h.run(body)


# -- refusal before upload ------------------------------------------------------------------------------------------------
def test_layout_over_8k_is_refused_before_any_byte_is_sent(h):
    h.panel = panel()

    async def body(p):
        assert (await p.import_package(h.package("big.zip", big_idle_zip("big-one", n_widgets=64, text_len=200))))["ok"]
        assert (await p.set_settings({"idle_screens": ["big-one"]}))["ok"]
        idle = await idle_state(p, {"error"})
        assert "big-one" in idle["error"] and "8192" in idle["error"] and idle["on_panel"] == []
        r = await p.push_idle_layouts()
        assert r["ok"] is False and "8192" in r["error"]
        assert h.panel.idle_frames == [] and h.panel.idle_store is None
        assert (await p.get_status())["connected"]
    h.run(body)


def test_store_over_48k_is_refused_before_any_byte_is_sent(h):
    h.panel = panel()

    async def body(p):
        ids = []
        for i in range(8):
            assert (await p.import_package(h.package(f"b{i}.zip", big_idle_zip(f"big-{i}"))))["ok"]
            ids.append(f"big-{i}")
        assert (await p.set_settings({"idle_screens": ids}))["ok"]
        idle = await idle_state(p, {"error"})
        assert "49152" in idle["error"] and h.panel.idle_frames == []
        # one screen fewer fits and goes out
        assert (await p.set_settings({"idle_screens": ids[:5]}))["ok"]
        idle = await idle_state(p, {"ok"})
        assert idle["on_panel"] == ids[:5] and h.panel.idle_commits == 1
    h.run(body)


def test_store_bigger_than_the_panels_reported_capacity_is_refused(h):
    h.panel = panel(idle_free=100)
    host_json(h)

    async def body(p):
        idle = await idle_state(p, {"error"})
        assert "the panel stores 100" in idle["error"] and h.panel.idle_frames == []
    h.run(body)


def test_screen_that_does_not_compile_is_reported_and_nothing_is_sent(h):
    h.panel = panel()

    async def body(p):
        doc = {"schema": 1, "id": "bad-idle", "name": "x", "version": "1.0.0", "author": "T",
               "idle": {"render_mode": "mono", "widgets": [{"type": "text", "x": 0, "y": 0, "w": 100, "h": 20,
                                                            "text": "{nosuchsource.value}"}]}}
        r = await p.import_package(h.package("bad.zip", package_zip("bad-idle", doc=doc)))
        if not r["ok"]:   # the package validator may already refuse it; either way nothing may reach the panel
            assert h.panel.idle_frames == []
            return
        assert (await p.set_settings({"idle_screens": ["bad-idle"]}))["ok"]
        idle = await idle_state(p, {"error"})
        assert "bad-idle" in idle["error"] and h.panel.idle_frames == []
    h.run(body)


# -- upload NAK ---------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("reason,name", [(P.NAK_STORAGE, "STORAGE"), (P.NAK_BAD_DATA, "BAD_DATA")])
def test_upload_nak_is_reported_and_the_display_keeps_working(h, reason, name):
    h.panel = panel()
    h.panel.idle_nak_with = reason
    host_json(h)

    async def body(p):
        idle = await idle_state(p, {"error"})
        assert name in idle["error"] and idle["on_panel"] == [] and h.panel.idle_store is None
        before = (await p.get_status())["frames_rendered"]
        await h.until(lambda: p.core.engine.status()["frames_rendered"] > before)   # no reconnect loop, no redraw storm
        st = await p.get_status()
        assert st["connected"] and st["reconnects"] == 0 and len(h.links) == 1
        assert name in st["last_error"]
        # the user retries: the panel accepts this time
        r = await p.push_idle_layouts()
        assert r["ok"] is True
        idle = await idle_state(p, {"ok"})
        assert idle["error"] is None and idle["on_panel"] == ["minimal-clock", "steam-stats"] and h.panel.idle_commits == 1
    h.run(body)


# -- idle timeout in MODE -----------------------------------------------------------------------------------------------
def test_idle_timeout_setting_is_pushed_and_validated(h):
    h.panel = panel()

    async def body(p):
        await h.until(lambda: modes(h))
        assert modes(h)[0] == b"\x00\x3c\x00"
        assert (await p.set_settings({"idle_timeout_s": 300}))["ok"]
        await h.until(lambda: len(modes(h)) == 2)
        assert modes(h)[1] == b"\x00\x2c\x01"   # 300 = 0x012c
        assert h.panel.idle_timeout_s == 300
        for bad in (14, 3601, "60", True):
            assert not (await p.set_settings({"idle_timeout_s": bad}))["ok"], bad
        assert len(modes(h)) == 2 and h.panel.idle_timeout_s == 300
        assert (await p.get_status())["idle"]["timeout_s"] == 300
    h.run(body)


# -- secrets --------------------------------------------------------------------------------------------------------------
def test_secrets_never_reach_logs_status_events_or_return_values(h):
    h.panel = panel()
    h.panel.idle_nak_with = P.NAK_STORAGE    # also cover the error path
    host_json(h)

    async def body(p):
        await idle_state(p, {"error"})
        out = [await p.get_status(), await p.get_settings(), await p.list_screens(), await p.push_idle_layouts(),
               await p.set_settings({"wifi_ssid": "Other"}), await p.set_settings({"wifi_pass": "p" * 64}),
               await p.set_settings({"wifi_pass": WIFI_PASS + "2"}), await p.get_status()]
        await h.until(lambda: h.panel.idle_commits >= 1)   # the NAK knob is spent: the next upload goes through
        await idle_state(p, {"ok"})
        dump = json.dumps(out) + json.dumps(await p.get_status())
        for sec in (*SECRETS, WIFI_PASS + "2"):
            assert sec not in dump
        assert (await p.get_settings())["wifi_pass"] == "set" and (await p.get_settings())["steam_api_key"] == "set"
        assert "ABCDEF" not in h.caplog.text and "hunter2" not in h.caplog.text
        assert "ABCDEF" not in json.dumps([e for e in h.events], default=str)
        assert "hunter2" not in json.dumps([e for e in h.events], default=str)
        # the one place they live: host.json (0600) and, on purpose, the panel's store
        assert h.host_file()["wifi_pass"] == WIFI_PASS + "2"
        assert parse_store(h.panel.idle_store)["config"]["steam_key"] == KEY
    h.run(body)


def test_uninstalling_a_selected_idle_screen_drops_it_from_the_store(h):
    h.panel = panel()

    async def body(p):
        assert (await p.import_package(h.package("mine.zip", big_idle_zip("mine"))))["ok"]
        assert (await p.set_settings({"idle_screens": ["minimal-clock", "mine"]}))["ok"]
        await h.until(lambda: p.core.engine.status()["idle"]["on_panel"] == ["minimal-clock", "mine"])
        assert (await p.uninstall_package("mine"))["ok"]
        await h.until(lambda: p.core.engine.status()["idle"]["on_panel"] == ["minimal-clock"])
        assert h.host_file()["idle_screens"] == ["minimal-clock"] and parse_store(h.panel.idle_store)["n_layouts"] == 1
    h.run(body)


def test_old_host_json_without_idle_keys_loads_with_defaults(h):
    h.panel = panel()
    h.settings_dir.mkdir(parents=True)
    (h.settings_dir / "host.json").write_text(json.dumps({"units": "metric", "idle_screens": []}))

    async def body(p):
        s = await p.get_settings()
        assert (s["idle_modes"], s["idle_rotate_s"], s["wifi_ssid"], s["wifi_pass"]) == ({}, 60, "", "unset")
        assert s["errors"] == []
    h.run(body)


def test_corrupt_idle_keys_in_host_json_are_quarantined(h):
    h.panel = panel()
    h.settings_dir.mkdir(parents=True)
    (h.settings_dir / "host.json").write_text(json.dumps({"idle_modes": {"x": "sepia"}, "wifi_pass": WIFI_PASS}))

    async def body(p):
        s = await p.get_settings()
        assert s["errors"] and "idle_modes" in s["errors"][0] and WIFI_PASS not in json.dumps(s)
        assert (h.settings_dir / "host.json.corrupt").exists()
    h.run(body)
