"""PluginCore and HostConfig edge cases that main.py never reaches in a normal run."""
import asyncio
import os
import time
from pathlib import Path

import pytest

from inkterface_core import plugin_core
from inkterface_core.plugin_core import HostConfig, PluginCore
from inkterface_core.screens import ScreenError


def test_host_config_validate_and_load_edges(tmp_path):
    assert HostConfig.validate([]) == ["top level must be an object"]
    assert HostConfig.validate({}) == []
    f = tmp_path / "host.json"
    f.write_text("[]")
    cfg, err = HostConfig.load(f)
    assert cfg == HostConfig() and "top level" in err and Path(str(f) + ".corrupt").exists()
    f.mkdir()   # a directory where the file should be: unreadable, reported, nothing destroyed
    cfg, err = HostConfig.load(f)
    assert cfg == HostConfig() and err.startswith("cannot read host.json") and f.is_dir()
    g = tmp_path / "g.json"
    g.write_bytes(b"\xff\xfe\x00")
    assert "using defaults" in HostConfig.load(g)[1]


def test_quarantine_failure_is_survivable(tmp_path, monkeypatch):
    f = tmp_path / "host.json"
    f.write_text("{bad")
    monkeypatch.setattr(plugin_core.os, "replace", lambda *a: (_ for _ in ()).throw(OSError("ro")))
    cfg, err = HostConfig.load(f)
    assert cfg == HostConfig() and "using defaults" in err


def test_save_cleans_its_temp_file_even_when_cleanup_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(plugin_core.os, "fsync", lambda fd: (_ for _ in ()).throw(OSError(5, "io")))
    with pytest.raises(OSError):
        HostConfig().save(tmp_path / "host.json")
    assert list(tmp_path.iterdir()) == []
    monkeypatch.setattr(plugin_core.os, "unlink", lambda p: (_ for _ in ()).throw(OSError("busy")))
    with pytest.raises(OSError, match="io"):   # the original error wins over the cleanup error
        HostConfig().save(tmp_path / "host.json")


def test_save_creates_a_private_directory(tmp_path):
    HostConfig(units="imperial").save(tmp_path / "new" / "host.json")
    assert oct(os.stat(tmp_path / "new").st_mode & 0o777) == "0o700"


def mk(tmp_path, h):
    return PluginCore(tmp_path / "s", Path(h.plugin_dir) / "screens", connect=h._connect, registry_factory=h._registry)


def test_start_twice_and_shutdown_twice(h, tmp_path):
    c = mk(tmp_path, h)
    with pytest.raises(RuntimeError, match="not running"):
        c.get_status()
    c.shutdown()   # before start: no-op
    c.start()
    e = c.engine
    c.start()      # second start keeps the same engine
    assert c.engine is e and len(h.registries) == 1
    c.shutdown()
    c.shutdown()
    with pytest.raises(RuntimeError, match="not running"):
        c.list_screens()
    assert h.leaked_threads == []


def test_engine_start_failure_stops_the_half_started_engine(h, tmp_path, monkeypatch):
    c = mk(tmp_path, h)
    monkeypatch.setattr(plugin_core.Engine, "start", lambda self: (_ for _ in ()).throw(RuntimeError("no thread")))
    with pytest.raises(RuntimeError, match="no thread"):
        c.start()
    assert c.engine is None and h.registries[0].stopped == 1 and h.leaked_threads == []


def test_shutdown_stops_the_sources_even_if_the_engine_stop_raises(h, tmp_path, monkeypatch):
    c = mk(tmp_path, h)
    c.start()
    eng = c.engine
    monkeypatch.setattr(eng, "stop", lambda: (_ for _ in ()).throw(RuntimeError("stuck")))
    with pytest.raises(RuntimeError, match="stuck"):
        c.shutdown()
    assert h.registries[0].stopped == 1
    monkeypatch.undo()
    eng.stop()   # the test's own cleanup of the real thread


def test_a_screen_that_fails_between_list_and_load_is_reported(h, tmp_path, monkeypatch):
    c = mk(tmp_path, h)
    real = plugin_core.load_screen

    def flaky(path, name=None):
        if name == "big-clock":
            raise ScreenError(["screen.json: changed under us"])
        return real(path, name)
    monkeypatch.setattr(plugin_core, "load_screen", flaky)
    c.start()
    try:
        assert "big-clock" not in c.engine.screens
        assert c.get_status()["screen_errors"] == ["big-clock: screen.json: changed under us"]
        entry = next(s for s in c.list_screens() if s["id"] == "big-clock")
        assert entry["error"] == "screen.json: changed under us" and not entry["has_active"]
    finally:
        c.shutdown()


def test_unknown_ids_are_refused_even_if_a_caller_skipped_the_screen_check(h, tmp_path):
    c = mk(tmp_path, h)
    c.start()
    try:
        r = c._save_engine_settings(lambda d: d.update(pinned="zzz"))
        assert not r["ok"] and "unknown screen 'zzz'" in r["error"] and c.engine.settings.pinned is None
    finally:
        c.shutdown()


def test_watcher_cancelled_mid_poll_unloads_cleanly(h):
    async def body(p):
        real = p.core.get_status
        started = []

        def slow():
            started.append(1)
            time.sleep(0.3)
            return real()
        p.core.get_status = slow
        await h.until(lambda: started)   # unload (leaving the context) now cancels the watcher mid-poll
    h.run(body)
