"""Engine.pause()/resume(): the firmware updater borrows the port."""
import time

import pytest
from engine_testlib import Rig

from inkterface_core import proto as P


@pytest.fixture
def rig(tmp_path):
    r = Rig(tmp_path)
    r.run(2)
    assert r.st["state"] == "ready"
    return r


def test_pause_closes_the_port_silently_and_keeps_the_known_firmware(rig):
    link, before = rig.link, len(rig.link.written)
    rig.engine.pause()
    st = rig.st
    assert st["state"] == "paused" and not st["connected"] and st["fw_version"] == "emulator"
    assert link.closed
    rig.run(10)   # no timers, no reconnect, nothing written: not even MODE PANEL
    assert len(link.written) == before and len(rig.links) == 1 and rig.st["state"] == "paused"
    assert rig.panel.mode == P.MODE_HOST


def test_resume_reconnects_at_once_and_redraws_everything(rig):
    rig.engine.pause()
    rig.engine.resume()
    rig.run(2)
    assert rig.st["state"] == "ready" and len(rig.links) == 2
    assert len(rig.frames(P.T_RECT)) == 10   # new session: full redraw, no assumption about the panel's contents
    assert rig.st["last_error"] is None


def test_resume_ignores_backoff(rig):
    rig.plugged = False
    rig.link.unplug()
    rig.run(3)
    assert rig.st["state"] == "disconnected"   # backing off
    rig.engine.pause()
    rig.plugged = True
    rig.engine.resume()
    rig.run(0.2)
    assert rig.st["state"] == "ready"


def test_pause_and_resume_when_unconnected_or_idempotent(tmp_path):
    r = Rig(tmp_path)
    r.engine.resume()   # not paused: no-op
    assert r.st["state"] == "disconnected"
    r.engine.pause()
    r.engine.pause()
    assert r.st["state"] == "paused" and not r.links
    r.engine.resume()
    r.engine.resume()
    r.run(2)
    assert r.st["state"] == "ready"


def test_resume_does_not_revive_a_stopped_engine(rig):
    rig.engine.stop()
    assert rig.st["state"] == "stopped"
    rig.engine.pause()   # stopped engines stay stopped
    rig.engine.resume()
    assert rig.st["state"] == "stopped"


def test_stop_while_paused_ends_stopped_without_touching_the_port(rig):
    rig.engine.pause()
    n = len(rig.link.written)
    rig.engine.stop()
    assert rig.st["state"] == "stopped" and len(rig.link.written) == n


def test_pause_survives_a_link_that_fails_to_close(rig):
    rig.link.close = lambda: (_ for _ in ()).throw(OSError("gone"))
    rig.engine.pause()
    assert rig.st["state"] == "paused"


def test_pause_from_another_thread_while_the_loop_runs(tmp_path):
    r = Rig(tmp_path, sleep=lambda s: time.sleep(0.005))   # real thread, real (fast) loop
    r.engine.start()
    try:
        deadline = time.monotonic() + 5
        while r.st["state"] != "ready":
            assert time.monotonic() < deadline
            time.sleep(0.01)
        r.engine.pause()
        n = len(r.link.written)
        time.sleep(0.2)
        assert r.st["state"] == "paused" and len(r.link.written) == n
        r.engine.resume()
        while r.st["state"] != "ready" or len(r.links) < 2:
            assert time.monotonic() < deadline
            time.sleep(0.01)
    finally:
        r.engine.stop()
