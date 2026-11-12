"""The "In a game" profile through main.py: toggle, copy on first enable, per-profile rotation / pin, cleanup, status."""
import json

import pytest


def test_old_settings_file_is_untouched_until_the_feature_is_used(h):
    h.settings_dir.mkdir()
    f = h.settings_dir / "settings.json"
    f.write_text(json.dumps({"schema": 1, "rotation": ["big-clock", "telemetry"], "rotation_interval_s": 20}))

    async def body(p):
        s = await p.get_settings()
        assert s["rotation"] == ["big-clock", "telemetry"] and s["ingame_enabled"] is False and s["ingame"] is None
        assert (await p.set_settings({"rotation_interval_s": 25}))["ok"]
        assert "ingame" not in json.loads(f.read_text()) and "ingame_enabled" not in json.loads(f.read_text())
        assert (await p.get_status())["profile"] == "normal"
    h.run(body)


def test_first_enable_copies_the_normal_profile_and_off_keeps_it(h):
    async def body(p):
        await p.set_rotation(["big-clock", "telemetry"], 20)
        await p.pin_screen("telemetry")
        assert (await p.set_settings({"ingame_enabled": True}))["ok"]
        s = await p.get_settings()
        assert s["ingame_enabled"] and s["ingame"] == {"rotation": ["big-clock", "telemetry"], "rotation_interval_s": 20, "pinned": "telemetry"}
        assert (await p.set_rotation(["thermals-power"], 15, "ingame"))["ok"]
        assert (await p.set_settings({"ingame_enabled": False}))["ok"]
        assert (await p.set_settings({"ingame_enabled": True}))["ok"]
        assert (await p.get_settings())["ingame"]["rotation"] == ["thermals-power"]   # not copied again
    h.run(body)


def test_profiles_are_edited_independently(h):
    async def body(p):
        await p.set_settings({"ingame_enabled": True})
        assert (await p.set_rotation(["now-playing", "big-clock"], 10, "ingame"))["ok"]
        assert (await p.pin_screen("now-playing", "ingame"))["ok"]
        s = await p.get_settings()
        assert s["rotation"] == ["telemetry", "thermals-power", "big-clock"] and s["pinned"] is None and s["rotation_interval_s"] == 30
        assert s["ingame"] == {"rotation": ["now-playing", "big-clock"], "rotation_interval_s": 10, "pinned": "now-playing"}
        assert (await p.unpin_screen("ingame"))["ok"] and (await p.get_settings())["ingame"]["pinned"] is None
        assert (await p.set_rotation(["telemetry"], None, "normal"))["ok"]
        assert (await p.get_settings())["ingame"]["rotation"] == ["now-playing", "big-clock"]
    h.run(body)


@pytest.mark.parametrize("profile", ["game", 1, True, ""])
def test_bad_profile_is_rejected_and_changes_nothing(h, profile):
    async def body(p):
        before = await p.get_settings()
        for r in (await p.set_rotation(["telemetry"], None, profile), await p.pin_screen("telemetry", profile), await p.unpin_screen(profile)):
            assert r["ok"] is False and "profile" in r["error"]
        assert await p.get_settings() == before
    h.run(body)


def test_game_uses_the_ingame_profile_and_status_says_so(h):
    async def body(p):
        await p.set_settings({"ingame_enabled": True})
        await p.set_rotation(["big-clock"], None, "ingame")
        await p.set_rotation(["telemetry"])
        await h.until(lambda: p.core.engine.status()["current_screen"] == "telemetry")
        assert (await p.set_now_playing(570))["ok"]
        await h.until(lambda: p.core.engine.status()["current_screen"] == "big-clock")
        assert (await p.get_status())["profile"] == "ingame"
    h.run(body)


def test_disabling_a_screen_unpins_it_in_the_ingame_profile(h):
    async def body(p):
        await p.set_settings({"ingame_enabled": True})
        await p.pin_screen("big-clock", "ingame")
        assert (await p.set_screen_enabled("big-clock", False))["ok"]
        assert (await p.get_settings())["ingame"]["pinned"] is None
    h.run(body)


def test_unknown_screen_in_ingame_profile_is_rejected(h):
    async def body(p):
        await p.set_settings({"ingame_enabled": True})
        assert (await p.set_rotation(["nope"], None, "ingame"))["ok"] is False
        assert (await p.pin_screen("nope", "ingame"))["ok"] is False
    h.run(body)
