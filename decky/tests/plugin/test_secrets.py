"""The Steam API key: stored 0600, never logged, never returned, never in an event or error."""
import json
import os
import stat

from inkterface_core import plugin_core
from inkterface_core.sources import SourceError

KEY = "ABCDEF0123456789ABCDEF0123456789"
BAD_KEY = "ZZZZ" + "SECRETSECRETSECRET" * 2   # wrong format: the error must not echo it


async def everything(p):
    """Call every read callable and return their JSON dumps joined."""
    out = [await p.get_status(), await p.get_settings(), await p.list_screens(), await p.preview_screen("telemetry"),
           await p.push_idle_layouts()]
    return json.dumps(out)


def test_key_is_stored_0600_and_reported_only_as_set(h):
    async def body(p):
        assert (await p.get_settings())["steam_api_key"] == "unset"
        r = await p.set_settings({"steam_api_key": KEY, "steamid64": "76561198000000000"})
        assert r["ok"] and r["settings"]["steam_api_key"] == "set"
        assert (await p.get_settings())["steam_api_key"] == "set"
        assert KEY not in json.dumps(r) and KEY not in await everything(p)
        mode = stat.S_IMODE(os.stat(h.settings_dir / "host.json").st_mode)
        assert mode == 0o600, oct(mode)
        assert h.host_file()["steam_api_key"] == KEY
        assert not (h.settings_dir / "settings.json").exists()   # engine settings never hold it
        assert h.registries[-1].cfg["steam_api_key"] == KEY   # the sources do get it
        assert not list(h.settings_dir.glob(".host-*"))      # no temp file left behind
    h.run(body)


def test_clearing_the_key(h):
    async def body(p):
        await p.set_settings({"steam_api_key": KEY})
        assert (await p.set_settings({"steam_api_key": ""}))["settings"]["steam_api_key"] == "unset"
        assert h.registries[-1].cfg["steam_api_key"] is None and h.host_file()["steam_api_key"] == ""
    h.run(body)


def test_replacing_the_key_keeps_the_mode(h):
    async def body(p):
        await p.set_settings({"steam_api_key": KEY})
        await p.set_settings({"steam_api_key": KEY.lower()})
        assert stat.S_IMODE(os.stat(h.settings_dir / "host.json").st_mode) == 0o600
    h.run(body)


def test_invalid_key_error_does_not_echo_the_value(h):
    async def body(p):
        r = await p.set_settings({"steam_api_key": BAD_KEY})
        assert not r["ok"] and "SECRET" not in json.dumps(r) and "32 hexadecimal" in r["error"]
        assert "SECRET" not in h.caplog.text
    h.run(body)


def test_key_never_reaches_logs_events_or_status(h):
    async def body(p):
        await p.set_settings({"steam_api_key": KEY, "steamid64": "76561198000000000"})
        p.core.engine._status.last_error = f"fetch https://api.steampowered.com/x?key={KEY} failed"   # a library echoing the URL
        st = await p.get_status()
        assert KEY not in st["last_error"] and "***" in st["last_error"]
        await p.set_settings({"units": "imperial"})
        await h.until(lambda: any("***" in str(a) for e, a in h.events if e == "status"))
    h.run(body)
    assert KEY not in h.caplog.text
    assert KEY not in json.dumps([[str(x) for x in a] for _, a in h.events], default=str)
    assert KEY not in "".join(r.getMessage() + str(r.args) for r in h.caplog.records)


def test_source_constructor_error_is_scrubbed(h):
    async def body(p):
        def boom(s):
            raise SourceError(f"bad key {KEY}")
        h.mod.registry_factory = boom
        r = await p.set_settings({"steam_api_key": KEY})
        assert not r["ok"] and KEY not in r["error"] and "***" in r["error"]
        assert not (h.settings_dir / "host.json").exists()
    h.run(body)


def test_key_survives_restart_without_being_exposed(h):
    async def first(p):
        await p.set_settings({"steam_api_key": KEY})
    h.run(first)

    async def second(p):
        assert (await p.get_settings())["steam_api_key"] == "set"
        assert h.registries[-1].cfg["steam_api_key"] == KEY
        assert KEY not in await everything(p)
    h.run(second)
    assert KEY not in h.caplog.text


def test_failed_save_leaves_no_world_readable_temp_file(h, monkeypatch):
    def fail(src, dst):
        raise OSError(5, "io error")
    async def body(p):
        monkeypatch.setattr(plugin_core.os, "replace", fail)
        r = await p.set_settings({"steam_api_key": KEY})
        assert not r["ok"] and KEY not in json.dumps(r)
        assert not list(h.settings_dir.glob(".host-*")) and not (h.settings_dir / "host.json").exists()
    h.run(body)


def test_corrupt_host_file_is_quarantined_not_logged(h):
    h.settings_dir.mkdir()
    (h.settings_dir / "host.json").write_text(json.dumps({"steam_api_key": KEY, "bogus": 1}))

    async def body(p):
        s = await p.get_settings()
        assert s["steam_api_key"] == "unset" and KEY not in json.dumps(s)
        assert KEY not in json.dumps(await p.get_status())
    h.run(body)
    assert KEY not in h.caplog.text
    assert (h.settings_dir / "host.json.corrupt").exists()
