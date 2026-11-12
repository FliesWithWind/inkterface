"""FirmwareManager corners that need fakes rather than the whole plugin."""
import threading

import pytest

from inkterface_core import firmware as FW
from inkterface_core import proto as P
from inkterface_core.ota import stream


class Eng:
    def __init__(self, fail_resume=False):
        self.calls, self.fail_resume = [], fail_resume

    def status(self):
        return {"fw_version": "1.0.0", "connected": True, "protocol_version": P.PROTO_VER}

    def pause(self):
        self.calls.append("pause")

    def resume(self):
        self.calls.append("resume")
        if self.fail_resume:
            raise RuntimeError("plugin is not running")


def mgr(engine=None, **kw):
    engine = engine or Eng()
    kw.setdefault("public_key", lambda: b"k" * 32)
    kw.setdefault("fetch", lambda url, n: None)
    return FW.FirmwareManager(lambda: engine, kw.pop("connect", lambda: None), plugin_version="1.0.0", **kw), engine


def test_start_twice_starts_one_thread_and_stop_is_repeatable():
    m, _ = mgr(tick_s=3600)
    m.start()
    t = m._thread
    m.start()
    assert m._thread is t
    m.stop()
    m.stop()
    assert not t.is_alive()


def test_scheduler_loop_survives_a_crashing_check(monkeypatch):
    m, _ = mgr(tick_s=0.01)
    n = []
    ev = threading.Event()

    def boom(auto=False):
        n.append(1)
        if len(n) >= 3:
            ev.set()
        raise RuntimeError("bug")

    monkeypatch.setattr(m, "check", boom)
    m.start()
    assert ev.wait(5), "loop died after the first exception"
    m.stop()


def test_unexpected_exception_in_check_becomes_a_status_error(monkeypatch):
    m, _ = mgr()
    monkeypatch.setattr(FW.github, "find_release", lambda *a, **k: (_ for _ in ()).throw(KeyError("x")))
    c = m.check()["check"]
    assert c["ok"] is False and c["error_kind"] == "KeyError" and "unexpected error: KeyError" in c["error"]
    assert m.check()["check"]["checking"] is False   # the flag is released


class Ser:
    def __init__(self, fail_close=False):
        self.closed, self.fail_close = False, fail_close

    def close(self):
        self.closed = True
        if self.fail_close:
            raise OSError("already gone")


@pytest.mark.parametrize("fail_close", [False, True])
def test_a_port_whose_handshake_fails_is_closed(monkeypatch, fail_close):
    ser = Ser(fail_close)
    m, _ = mgr(connect=lambda: ser, handshake_timeout=0.01)
    monkeypatch.setattr(stream, "handshake", lambda link, timeout: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        m._open_panel()
    assert ser.closed


def test_connect_errors_are_worded_for_the_user():
    m, _ = mgr(connect=lambda: (_ for _ in ()).throw(FileNotFoundError("no panel found (USB VID 303a)")))
    with pytest.raises(OSError, match="cannot open the panel: no panel found"):
        m._open_panel()


def test_engine_gone_at_the_end_of_an_install_is_not_an_error(monkeypatch):
    m, eng = mgr(Eng(fail_resume=True))
    monkeypatch.setattr(FW.github, "download_image", lambda fetch, rel: b"\xe9")
    monkeypatch.setattr(FW.stream, "install", lambda *a, **k: (_ for _ in ()).throw(FW.Cancelled("stop")))

    class Rel:
        class manifest:
            version = "1.2.0"
            size = 1

    m._install = {**m._idle_install(), "state": FW.DOWNLOADING}
    m._run_install(Rel)   # must not raise although resume() does
    assert eng.calls == ["pause", "resume"] and m._install["state"] == FW.CANCELLED


def test_oserror_without_text_still_gives_a_message(monkeypatch):
    m, eng = mgr()
    monkeypatch.setattr(FW.github, "download_image", lambda fetch, rel: b"\xe9")
    monkeypatch.setattr(FW.stream, "install", lambda *a, **k: (_ for _ in ()).throw(OSError(5, "Input/output error")))

    class Rel:
        class manifest:
            version = "1.2.0"
            size = 1

    m._install = {**m._idle_install(), "state": FW.DOWNLOADING}
    m._run_install(Rel)
    assert m._install["state"] == FW.FAILED and "Input/output error" in m._install["error"]
    m._install["state"] = FW.DOWNLOADING
    monkeypatch.setattr(FW.stream, "install", lambda *a, **k: (_ for _ in ()).throw(OSError()))
    m._run_install(Rel)
    assert m._install["state"] == FW.FAILED and m._install["error"]
