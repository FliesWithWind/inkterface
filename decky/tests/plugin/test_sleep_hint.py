"""The plugin wires the logind sleep hint to the engine (tests/test_sleep_idle.py has the rest)."""
import asyncio
from pathlib import Path

import pytest

from inkterface_core import proto as P

class FakeWatch:
    made = []

    def __init__(self, on_sleep, on_wake):
        self.on_sleep, self.on_wake, self.started, self.stopped = on_sleep, on_wake, False, False
        FakeWatch.made.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


def test_plugin_wires_the_hint_to_the_engine_and_stops_it_first(h):
    FakeWatch.made.clear()
    h.mod.sleep_watch = FakeWatch
    order = []

    async def body(p):
        w, = FakeWatch.made
        assert w.started and not w.stopped
        assert await asyncio.to_thread(w.on_sleep) is True
        assert h.panel.mode == P.MODE_PANEL
        st = await p.get_status()
        assert st["ok"] if "ok" in st else True
        await asyncio.to_thread(w.on_wake)
        await h.until(lambda: h.panel.mode == P.MODE_HOST)
        await h.until(lambda: p.core.engine.status()["last_frame_at"] is not None)
        eng = p.core.engine
        w.stop = lambda: order.append(("hint", eng.status()["state"]))

    h.run(body)
    assert order == [("hint", "ready")]       # stopped while the engine still ran: a leaving plugin releases the lock first


def test_plugin_without_a_hint_runs_as_before(h):
    h.mod.sleep_watch = None

    async def body(p):
        assert p.core.sleep_hint is None

    h.run(body)


def test_plugin_start_failure_stops_the_hint(tmp_path):
    from inkterface_core.plugin_core import PluginCore

    class Boom(FakeWatch):
        def start(self):
            super().start()
            raise RuntimeError("no bus")

    FakeWatch.made.clear()
    core = PluginCore(tmp_path / "s", Path(__file__).resolve().parents[1] / "screens", connect=lambda: (_ for _ in ()).throw(OSError("no panel")),
                      registry_factory=lambda s: __import__("inkterface_core.sources", fromlist=["Registry"]).Registry([]),
                      sleep_watch=Boom)
    with pytest.raises(RuntimeError, match="no bus"):
        core.start()
    assert FakeWatch.made[0].stopped and core.engine is None and core.sleep_hint is None


