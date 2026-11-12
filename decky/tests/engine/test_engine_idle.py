"""Engine side of the idle store upload: chunking, ordering, abort/restart, NAK handling, MODE timeout, status."""
import pytest
from engine_testlib import Rig

from inkterface_core import proto as P
from inkterface_core.emulator import EmulatedPanel
from inkterface_core.engine import Settings
from inkterface_core.engine import engine as E
from inkterface_core.idle.compile import compile_layout
from inkterface_core.idle.plan import IdlePlan
from inkterface_core.idle.store import pack_store

CAPS = P.CAP_GRAY4 | P.CAP_IDLE_STORAGE


def layout(tag, n=40, text_len=100, mode="mono"):
    ws = [{"type": "text", "x": 0, "y": (i % 20) * 20, "w": 600, "h": 20, "text": f"{tag}.{i:03d}".ljust(text_len, "x")}
          for i in range(n)]
    return compile_layout({"render_mode": mode, "widgets": ws})


def plan(*tags, ids=None, **kw):
    blob = pack_store([layout(t) for t in tags], config={"tz": "UTC0"})
    return IdlePlan(blob=blob, ids=tuple(ids or tags), modes=("mono",) * len(tags), **kw)


def rig(tmp_path, plan_=None, **panel_kw):
    panel_kw.setdefault("caps", CAPS)
    panel_kw.setdefault("idle_free", 49152)
    r = Rig(tmp_path, panel=EmulatedPanel(**panel_kw))
    if plan_ is not None:
        r.engine.set_idle_plan(plan_)
    return r


def idle_frames(r):
    return [(f.payload[:4], f.payload[4:8]) for f in r.frames(P.T_IDLE_LAYOUT)]


def test_chunks_go_out_in_order_after_the_extended_mode(tmp_path):
    p = plan("a", "b")
    assert len(p.blob) > 2 * E.IDLE_CHUNK        # at least three chunks
    r = rig(tmp_path, p)
    r.run(3)
    types = [f.type for f in r.link.written]
    assert types.index(P.T_MODE) < types.index(P.T_IDLE_LAYOUT)
    assert r.link.written[types.index(P.T_MODE)].payload == b"\x00\x3c\x00"   # MODE HOST, 60 s
    offsets = [int.from_bytes(f.payload[4:8], "little") for f in r.frames(P.T_IDLE_LAYOUT)]
    assert offsets == list(range(0, len(p.blob), E.IDLE_CHUNK))
    assert {int.from_bytes(f.payload[:4], "little") for f in r.frames(P.T_IDLE_LAYOUT)} == {len(p.blob)}
    assert r.panel.idle_store == p.blob and r.panel.idle_commits == 1
    s = r.st["idle"]
    assert s["state"] == "ok" and s["on_panel"] == ["a", "b"] and s["bytes"] == len(p.blob) and s["error"] is None
    assert r.st["naks"] == 0 and r.st["frames_rendered"] > 0   # the display went on


def test_the_status_never_carries_the_blob(tmp_path):
    p = plan("a")
    r = rig(tmp_path, p)
    r.run(2)
    assert p.blob not in repr(r.st).encode() and "blob" not in r.st["idle"] and p.blob.hex() not in repr(p)


def test_no_plan_and_a_plan_without_blob_send_nothing(tmp_path):
    r = rig(tmp_path)
    r.run(2)
    assert r.st["idle"]["state"] == "none" and r.panel.idle_frames == []
    r.engine.set_idle_plan(IdlePlan(ids=("a",), error="a: broken layout"))
    r.run(2)
    assert r.st["idle"]["state"] == "error" and r.st["idle"]["error"] == "a: broken layout" and r.panel.idle_frames == []
    r.engine.set_idle_plan(None)
    r.run(1)
    assert r.st["idle"]["state"] == "none"


def test_a_panel_without_idle_storage_is_unsupported_and_gets_a_bare_mode(tmp_path):
    r = rig(tmp_path, plan("a"), caps=0, idle_free=0)
    r.run(2)
    s = r.st["idle"]
    assert s["state"] == "unsupported" and "update the firmware" in s["error"] and r.panel.idle_frames == []
    assert r.link.written[[f.type for f in r.link.written].index(P.T_MODE)].payload == b"\x00"


def test_gray4_layout_needs_the_gray4_capability(tmp_path):
    blob = pack_store([layout("g", mode="gray4")])
    r = rig(tmp_path, IdlePlan(blob=blob, ids=("g",), modes=("gray4",), needs_gray4=True), caps=P.CAP_IDLE_STORAGE)
    r.run(2)
    assert r.st["idle"]["state"] == "error" and "gray4" in r.st["idle"]["error"] and r.panel.idle_frames == []


def test_store_larger_than_the_panel_capacity_is_refused(tmp_path):
    p = plan("a")
    r = rig(tmp_path, p, idle_free=len(p.blob) - 1)
    r.run(2)
    assert r.st["idle"]["state"] == "error" and f"the panel stores {len(p.blob) - 1}" in r.st["idle"]["error"]
    assert r.panel.idle_frames == []


def test_nak_on_the_last_chunk_reports_the_reason_without_touching_the_display(tmp_path):
    p = plan("a", "b")
    r = rig(tmp_path, p)
    r.panel.idle_nak_with = P.NAK_STORAGE
    r.run(3)
    s = r.st
    assert s["idle"]["state"] == "error" and "STORAGE" in s["idle"]["error"] and s["idle"]["on_panel"] == []
    assert s["state"] == "ready" and s["naks"] == 0 and s["reconnects"] == 0 and len(r.links) == 1
    assert r.panel.idle_store is None
    r.run(3)
    assert r.st["idle"]["state"] == "error"          # no automatic retry loop
    r.engine.set_idle_plan(p, force=True)            # the user retries
    r.run(3)
    assert r.st["idle"]["state"] == "ok" and r.panel.idle_commits == 1


def test_nak_on_the_first_chunk_ignores_the_chunks_already_in_flight(tmp_path):
    p = plan("a", "b")
    r = rig(tmp_path, p)
    # an upload whose declared total is wrong for the panel: it NAKs the first chunk, the second is already on the wire
    r.panel.caps = CAPS
    orig = r.panel._idle_layout

    def refuse_first(f):
        if int.from_bytes(f.payload[4:8], "little") == 0:
            return r.panel._nak(f.seq, P.NAK_BAD_DATA)
        return orig(f)

    r.panel._idle_layout = refuse_first
    r.run(3)
    assert r.st["idle"]["state"] == "error" and "BAD_DATA" in r.st["idle"]["error"]
    assert r.st["naks"] == 0 and r.st["state"] == "ready" and len(r.links) == 1


def test_changing_the_plan_mid_upload_drops_the_old_chunks_and_sends_the_new_store(tmp_path):
    old, new = plan("a", "b", "c"), plan("x")
    r = rig(tmp_path, old)
    r.run(0.1)                                  # handshake + MODE done, first chunks are on the wire
    r.engine.set_idle_plan(new)
    r.run(3)
    assert r.panel.idle_store == new.blob
    assert r.st["idle"]["state"] == "ok" and r.st["idle"]["on_panel"] == ["x"]
    assert r.panel.idle_commits == 1            # the old upload never completed


def test_unplugged_mid_upload_restarts_from_zero_after_reconnect(tmp_path):
    p = plan("a", "b", "c")
    r = rig(tmp_path, p)
    r.run(0.15)
    assert r.st["idle"]["state"] == "uploading"
    r.link.unplug()
    r.run(0.2)
    assert r.st["state"] == "disconnected" and r.st["idle"]["state"] == "pending"
    r.run(8)
    assert r.st["state"] == "ready" and r.st["idle"]["state"] == "ok" and r.panel.idle_store == p.blob
    assert r.panel.idle_frames.count((len(p.blob), 0, E.IDLE_CHUNK)) >= 2   # offset 0 was sent again


def test_a_silent_panel_during_the_upload_times_out_and_reconnects(tmp_path):
    p = plan("a", "b")
    r = rig(tmp_path, p)
    r.run(0.1)
    r.link.mute = True
    r.run(E.IDLE_ACK_TIMEOUT_S + 1)
    assert r.st["state"] != "ready" or r.st["idle"]["state"] != "ok"
    assert "did not answer idle" in (r.st["last_error"] or "")
    r.run(12)
    assert r.st["idle"]["state"] == "ok" and r.panel.idle_store == p.blob


def test_every_connection_uploads_again_but_a_resend_of_the_same_plan_does_not(tmp_path):
    p = plan("a")
    r = rig(tmp_path, p)
    r.run(2)
    r.engine.set_idle_plan(plan("a"))           # identical bytes: no-op
    r.run(2)
    assert r.panel.idle_commits == 1
    r.engine.pause()
    r.engine.resume()
    r.run(3)
    assert r.panel.idle_commits == 2 and r.st["idle"]["state"] == "ok"


def test_unsolicited_hello_during_the_upload_restarts_it(tmp_path):
    p = plan("a", "b", "c")
    r = rig(tmp_path, p)
    r.run(0.15)
    assert r.st["idle"]["state"] == "uploading"
    r.link._out.append((0.0, r.panel._hello()))   # the panel lost our session: it says HELLO by itself
    r.run(3)
    assert r.st["idle"]["state"] == "ok" and r.panel.idle_store == p.blob
    assert r.panel.idle_frames.count((len(p.blob), 0, E.IDLE_CHUNK)) == 2   # restarted from offset 0


def test_idle_timeout_setting_resends_mode_and_keeps_the_store(tmp_path):
    r = rig(tmp_path, plan("a"))
    r.run(2)
    r.engine.update_settings(Settings(rotation=["a", "b"], rotation_interval_s=30, idle_timeout_s=600))
    r.run(1)
    assert r.panel.mode_frames == [b"\x00\x3c\x00", b"\x00\x58\x02"]
    assert r.panel.idle_timeout_s == 600 and r.panel.idle_commits == 1
    r.engine.update_settings(Settings(rotation=["a"], rotation_interval_s=30, idle_timeout_s=600))   # unchanged value
    r.run(1)
    assert len(r.panel.mode_frames) == 2


def test_status_is_pending_while_disconnected(tmp_path):
    r = rig(tmp_path, plan("a"))
    r.run(2)
    assert r.st["idle"]["state"] == "ok"
    r.link.unplug()
    r.run(0.2)
    assert r.st["idle"]["state"] == "pending" and r.st["idle"]["on_panel"] == ["a"]   # last acknowledged store
