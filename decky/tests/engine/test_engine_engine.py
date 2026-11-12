import time
from pathlib import Path

import pytest
from engine_testlib import INVERT, Clock, Data, Rig, make_screens, write_screen

from inkterface_core import diff as D
from inkterface_core import engine as E
from inkterface_core import proto as P
from inkterface_core.emulator import EmulatedPanel
from inkterface_core.engine import Engine, EmulatedLink, Settings, load_screens, usb_connect
from inkterface_core.engine import engine as engine_mod
from inkterface_core.screens import render_frame

FULL_REFRESH = ("F", (0, 0, 648, 480))


def refresh_kinds(rig):
    return "".join(k for k, _, _ in rig.panel.refreshes)


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


# ---------------------------------------------------------------- connect + first frame

def test_first_frame_is_a_full_redraw_that_matches_the_render_exactly(rig):
    rig.run(2)
    st = rig.st
    assert st["state"] == "ready" and st["connected"] and st["fw_version"] == "emulator"
    assert st["panel_size"] == (648, 480) and st["protocol_version"] == 1
    assert rig.panel.mode == P.MODE_HOST
    assert rig.panel.glass == bytearray(rig.expected("a"))
    assert bytes(rig.panel.glass).count(0xFF) > 0 and any(rig.panel.glass)  # real ink, black = 1
    assert [(k, box) for k, box, _ in rig.panel.refreshes] == [FULL_REFRESH]
    assert len(rig.frames(P.T_RECT)) == 10  # full-frame bands (9 x 50 rows + 30)
    assert st["rects_sent"] == 10 and st["full_refreshes"] == 1 and st["partial_refreshes"] == 0
    assert st["current_screen"] == "a" and st["last_frame_at"] is not None and st["last_error"] is None


def test_wire_order_is_hello_mode_rects_refresh(rig):
    rig.run(2)
    kinds = [f.type for f in rig.link.written]
    assert kinds[0] == P.T_HELLO and kinds[1] == P.T_MODE
    assert kinds[2:12] == [P.T_RECT] * 10 and kinds[12] == P.T_REFRESH


def test_sequence_numbers_increment(rig):
    rig.run(2)
    assert [f.seq for f in rig.link.written] == list(range(len(rig.link.written)))


# ---------------------------------------------------------------- partial updates

def test_changed_value_sends_only_the_changed_rects(rig):
    rig.run(2)
    old = rig.expected("a")
    n_before, bytes_before = len(rig.frames(P.T_RECT)), rig.wire_bytes(P.T_RECT)
    rig.data.d["system.cpu.load"] = 77.0
    rig.run(3)
    new = rig.expected("a")
    assert old != new and rig.panel.glass == bytearray(new)
    want = D.diff_rects(old, new)
    assert want and len(want) <= 2
    sent = rig.frames(P.T_RECT)[n_before:]
    assert [tuple(int.from_bytes(f.payload[i:i + 2], "little") for i in (0, 2, 4, 6)) for f in sent] == want
    sent_bytes = rig.wire_bytes(P.T_RECT) - bytes_before
    assert sent_bytes == sum(18 + h * (w // 8) for _, _, w, h in want)
    assert sent_bytes < 2000 < 39000  # not a full frame
    assert [k for k, _, _ in rig.panel.refreshes] == ["F", "P"]
    assert rig.st["partial_refreshes"] == 1


def test_two_separate_changes_give_two_rects(rig):
    rig.run(2)
    rig.data.d.update({"system.cpu.load": 55.0, "system.gpu.load": 99.0})
    n = len(rig.frames(P.T_RECT))
    rig.run(3)
    assert len(rig.frames(P.T_RECT)) - n == 2
    assert rig.panel.glass == bytearray(rig.expected("a"))


def test_unchanged_data_sends_nothing(rig):
    rig.run(2)
    n = len(rig.link.written)
    st0 = rig.st
    rig.run(3)  # renders every second, finds no difference
    new = [f.type for f in rig.link.written[n:]]
    assert P.T_RECT not in new and P.T_REFRESH not in new
    assert rig.st["unchanged_ticks"] > st0["unchanged_ticks"]
    assert rig.st["frames_rendered"] > st0["frames_rendered"]


def test_screen_refresh_s_sets_the_render_rate(tmp_path):
    r = Rig(tmp_path, Settings(rotation=["now-playing"], rotation_interval_s=30))  # refresh_s 5
    r.run(21)
    assert 4 <= r.st["frames_rendered"] <= 6


# ---------------------------------------------------------------- full vs partial policy

def test_screen_switch_is_a_full_refresh_with_only_the_changed_rects(rig):
    rig.run(2)
    a = rig.expected("a")
    n, b0 = len(rig.frames(P.T_RECT)), rig.wire_bytes(P.T_RECT)
    rig.engine.update_settings(Settings(rotation=["a", "b"], pinned="b"))
    rig.run(3)
    assert rig.st["current_screen"] == "b"
    assert rig.panel.glass == bytearray(rig.expected("b"))
    assert [k for k, _, _ in rig.panel.refreshes] == ["F", "F"]
    assert rig.panel.refreshes[1][1] == (0, 0, 648, 480)
    sent = rig.wire_bytes(P.T_RECT) - b0
    assert 0 < sent < 39000  # diff against the previous screen, not a full re-send
    assert sent == sum(18 + h * (w // 8) for _, _, w, h in D.diff_rects(a, rig.expected("b")))


def test_rotation_switches_on_time(rig):
    rig.run(29)
    assert rig.st["current_screen"] == "a"
    rig.run(3)  # t=32 > 30
    assert rig.st["current_screen"] == "b"
    assert rig.panel.glass == bytearray(rig.expected("b"))
    rig.run(30)
    assert rig.st["current_screen"] == "a" and rig.panel.glass == bytearray(rig.expected("a"))
    assert refresh_kinds(rig) == "FFF"


def burst(r, n, start=0, gap=0.3):
    """n renders that each carry a change, the engine told by a notification like a registry would (so no quiet render in between)."""
    for i in range(n):
        r.data.change(**{"system__cpu__load": float(1 + (start + i) * 7 % 97)})
        r.run(gap)


@pytest.fixture
def few(monkeypatch):
    """Count gate lowered to 3 partials so a test needs seconds, not hundreds of renders. The time gate is real (1 min = 60 s)."""
    monkeypatch.setattr(engine_mod, "GHOST_MIN_PARTIALS", 3)


def ghost_rig(tmp_path, minutes=1, **kw):
    r = Rig(tmp_path, Settings(rotation=["a"], ghost_clear_minutes=minutes), **kw)
    r.run(2)
    return r


def test_ghost_phase_table():
    gp, n = engine_mod.ghost_phase, engine_mod.GHOST_MIN_PARTIALS
    assert gp(0, 10_000, 10_000_000) == "off"                      # 0 = never, whatever else is true
    assert gp(10, n - 1, 3600) == "wait"                           # time is up, too few partials: nothing worth clearing
    assert gp(10, n, 599.9) == "wait"                              # enough partials, too soon
    assert gp(10, n, 600) == "due"                                 # both gates open
    assert gp(10, n, 600 * engine_mod.GHOST_DEFER_LIMIT - 0.1) == "due"
    assert gp(10, n, 600 * engine_mod.GHOST_DEFER_LIMIT) == "forced"
    assert gp(10, n - 1, 10 ** 7) == "wait"                         # the cap does not skip the count gate
    assert gp(1, n, 60) == "due" and gp(120, n, 7200) == "due"


def test_ghost_clear_default_is_10_minutes():
    assert Settings().ghost_clear_minutes == 10 and engine_mod.GHOST_MIN_PARTIALS == 30


def test_count_gate_alone_does_not_clear(tmp_path, few):
    r = ghost_rig(tmp_path)
    burst(r, 12)   # 12 partials in ~4 s: far past the count gate, nowhere near a minute
    r.run(3)
    assert refresh_kinds(r) == "F" + "P" * 12 and r.st["ghost_deferred"] == 0


def test_time_gate_alone_does_not_clear(tmp_path, few):
    r = ghost_rig(tmp_path)
    burst(r, 2)    # 2 partials < 3
    r.run(120)     # two minutes of a quiet screen
    assert refresh_kinds(r) == "FPP"


def test_clear_runs_at_the_first_quiet_render_once_both_gates_are_open(tmp_path, few):
    r = ghost_rig(tmp_path)
    burst(r, 4)
    r.run(62)      # >= 60 s since the full refresh, nothing changing: the quiet moment
    assert refresh_kinds(r) == "FPPPPF"
    assert r.panel.glass == bytearray(r.expected("a"))
    r.run(5)
    assert refresh_kinds(r) == "FPPPPF"   # one is enough: partials and timer started again
    assert r.st["ghost_deferred"] == 0


def test_busy_screen_defers_then_hits_the_cap(tmp_path, few):
    r = ghost_rig(tmp_path)   # full refresh acknowledged at ~t0; due at +60 s, forced at +90 s
    t0 = r.clock.t
    i = 0
    while r.clock.t - t0 < 100:
        burst(r, 1, start=i)
        i += 1
    kinds = refresh_kinds(r)
    assert kinds.count("F") == 2 and kinds[0] == "F"
    forced_at = kinds.index("F", 1)
    assert forced_at > 100       # a full cannot come before ~60 s of 0.3 s renders...
    assert r.st["ghost_deferred"] > 0   # ...and between 60 s and 90 s it was due but deferred
    # the forced full came at ~90 s (1.5 x 60 s), not at 60 s
    assert r.st["ghost_deferred"] == pytest.approx((90 - 60) / 0.3, abs=6)


def test_screen_switch_restarts_the_timer(tmp_path, few):
    r = Rig(tmp_path, Settings(rotation=["a", "b"], rotation_interval_s=40, ghost_clear_minutes=1))
    r.run(2)
    burst(r, 4)
    r.run(42)      # t ~ 45 s: the screen switched to b at 40 s (a full refresh) and restarted the timer
    r.data.change(**{"system__cpu__load": 3.0})
    r.run(1)
    assert refresh_kinds(r) == "FPPPPFP"
    r.run(28)      # t ~ 74 s: past 60 s since the FIRST full refresh, with 5 partials, but only ~33 s since the switch
    assert refresh_kinds(r) == "FPPPPFP"


def test_zero_never_clears(tmp_path, few):
    r = ghost_rig(tmp_path, minutes=0)
    burst(r, 40, gap=0.5)
    r.run(900)
    assert refresh_kinds(r) == "F" + "P" * 40


def test_clock_going_backwards_restarts_the_timer(tmp_path, few):
    r = ghost_rig(tmp_path)
    t = r.clock.t
    assert 29 < r.engine._since_full(t + 30) < 33          # normal: elapsed time
    assert r.engine._since_full(t - 10_000) == 0.0          # injected clock jump back: never negative, never "already forced"
    assert r.engine._since_full(t - 10_000 + 25) == 25.0    # and the timer counts from the new reading
    assert engine_mod.ghost_phase(1, 99, r.engine._since_full(t - 10_000 + 25)) == "wait"


def test_timer_starts_at_the_acknowledged_full_refresh_not_at_connect(tmp_path, few):
    r = ghost_rig(tmp_path, full_s=4.0)   # the full refresh takes 4 s on the emulated panel
    r.run(5)
    burst(r, 4)
    r.run(55.5)    # ~ 60 s after the connect but < 60 s after the acknowledged full refresh
    assert refresh_kinds(r) == "FPPPP"
    r.run(10)
    assert refresh_kinds(r) == "FPPPPF"


def test_reconnect_restarts_the_timer(tmp_path, few):
    r = ghost_rig(tmp_path)
    burst(r, 4)
    r.run(45)
    r.link.unplug()   # port lost: the engine reconnects and draws a full frame
    r.run(20)
    assert r.engine._since_full(r.clock.t) < 20 + 1e-6
    assert refresh_kinds(r).count("F") == 2


def test_deferral_never_delays_other_full_refreshes(tmp_path, few):
    r = Rig(tmp_path, Settings(rotation=["a", "b"], rotation_interval_s=30, ghost_clear_minutes=1))
    r.run(2)
    burst(r, 4)
    r.engine.update_settings(Settings(rotation=["a", "b"], pinned="b", ghost_clear_minutes=1))
    r.run(2)
    assert refresh_kinds(r)[-1] == "F"   # a screen switch is a full refresh at once
    assert r.panel.glass == bytearray(r.expected("b"))


# ---------------------------------------------------------------- heartbeat

def test_heartbeat_every_5s_when_nothing_changes(rig):
    rig.run(2)
    hb0 = len(rig.frames(P.T_HEARTBEAT))
    rig.run(10.9)
    n = len(rig.frames(P.T_HEARTBEAT)) - hb0
    assert n == 2  # t~7 and t~12 relative to the last frame
    assert rig.st["heartbeats_sent"] == len(rig.frames(P.T_HEARTBEAT))
    assert rig.st["connected"]


def test_no_heartbeat_needed_while_frames_flow(rig):
    for i in range(8):
        rig.data.d["system.cpu.load"] = float(10 + i * 9)
        rig.run(2)
    assert rig.frames(P.T_HEARTBEAT) == []  # every frame already counts as liveness


def test_heartbeat_gap_is_5s_when_idle(tmp_path):
    rig = Rig(tmp_path, Settings(rotation=["a"]))
    times = []
    rig.run(1)
    link = rig.link
    orig_write = link.write

    def spy(data, timeout=None):
        times.append(rig.clock.t)
        orig_write(data, timeout)

    link.write = spy
    rig.run(40)
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert gaps and max(gaps) <= 5.1 and min(gaps) >= 4.9


def test_long_idle_run_survives_seq_wraparound(tmp_path):
    rig = Rig(tmp_path, Settings(rotation=["a"]))
    rig.run(1700, dt=0.25)  # ~320 heartbeats: seq wraps at 256
    assert rig.st["connected"] and rig.st["heartbeats_sent"] > 300 and rig.st["naks"] == 0
    assert rig.engine._seq < 100 and rig.st["reconnects"] == 0


# ---------------------------------------------------------------- unplug / reconnect

def test_unplug_mid_frame_then_reconnect_does_a_full_redraw(rig):
    rig.run(2)
    old_panel_glass = bytes(rig.panel.glass)
    rig.data.d["system.cpu.load"] = 88.0
    rig.link.fail_after = 100  # cut in the middle of the next RECT
    rig.plugged = False
    rig.run(1.5)
    st = rig.st
    assert st["state"] == "disconnected" and not st["connected"]
    assert "unplugged mid-write" in st["last_error"]
    assert bytes(rig.panel.glass) == old_panel_glass  # the cut frame never reached the glass
    rig.run(10)
    assert rig.st["state"] == "disconnected" and rig.st["retry_in_s"] is not None
    rig.plugged = True
    rig.data.d["system.cpu.load"] = 31.0
    rig.run(15)
    st = rig.st
    assert st["state"] == "ready" and st["reconnects"] == 1
    assert rig.panel.glass == bytearray(rig.expected("a"))
    last_full = [f for f in rig.link.written if f.type == P.T_RECT]
    assert len(last_full) == 10  # whole frame again on the new link (shadow state unknown)
    assert rig.panel.refreshes[-1][0] == "F"
    assert len(rig.links) == 2 and rig.links[0].closed


def test_unplug_between_frames_via_read_error(rig):
    rig.run(2)
    rig.link.unplug()
    rig.run(0.2)
    assert rig.st["state"] == "disconnected" and "disconnected" in rig.st["last_error"]


def test_backoff_doubles_from_2_to_60_seconds(rig):
    rig.plugged = False
    rig.run(400, dt=0.25)
    t = rig.connect_times
    gaps = [round(b - a) for a, b in zip(t, t[1:])]
    assert gaps[:8] == [2, 4, 8, 16, 32, 60, 60, 60]
    assert "no panel found" in rig.st["last_error"]


def test_backoff_resets_after_a_good_connection(rig):
    rig.plugged = False
    rig.run(20, dt=0.25)  # failed a few times: next wait is large
    rig.plugged = True
    rig.run(70, dt=0.25)
    assert rig.st["connected"]
    rig.link.unplug()
    rig.plugged = False
    t0 = rig.clock.t
    n0 = len(rig.connect_times)
    rig.run(10, dt=0.25)
    first_retry = rig.connect_times[n0] - t0 if len(rig.connect_times) > n0 else None
    assert first_retry is not None and first_retry <= 2.5


def test_status_reports_retry_countdown(rig):
    rig.plugged = False
    rig.run(0.1)
    s1 = rig.st
    assert s1["state"] == "disconnected" and 0 < s1["retry_in_s"] <= 2
    rig.run(1)
    assert rig.st["retry_in_s"] < s1["retry_in_s"]


def test_reconnect_reuses_the_same_panel_state_but_redraws(rig):
    rig.run(2)
    rig.link.unplug()
    rig.run(8)
    assert rig.st["connected"]
    assert rig.links[1].panel is rig.links[0].panel
    assert refresh_kinds(rig) == "FF"


def test_hello_times_out_when_the_panel_never_answers(tmp_path):
    class Deaf(EmulatedPanel):
        def feed(self, data):
            return b""

    r = Rig(tmp_path, panel=Deaf())
    r.run(29)
    assert r.st["state"] == "handshake"
    hellos = len([f for f in r.link.written if f.type == P.T_HELLO])
    assert 14 <= hellos <= 16  # one every 2 s
    r.run(3)
    assert r.st["state"] == "disconnected" and "no HELLO" in r.st["last_error"]
    assert not any(f.type in (P.T_RECT, P.T_REFRESH, P.T_MODE) for f in r.link.written)


def test_duplicate_hello_replies_to_our_retries_do_not_reset_the_session(rig):
    rig.run(0.5)
    # panel answered once; a late duplicate (retry crossing the reply) must not look like a panel reset
    rig.engine._hellos_expected = 1
    rig.link._out.append((0, P.encode(P.T_HELLO, 9, P.hello_payload(P.Hello(1, 4096, 8192, 648, 480, 0, 0, "emulator")))))
    rig.run(3)
    assert refresh_kinds(rig) == "F"


# ---------------------------------------------------------------- protocol mismatch

class OldFirmware(EmulatedPanel):
    ver, size = 2, (648, 480)

    def _hello(self):
        h = P.Hello(self.ver, P.MAX_PAYLOAD, self.rx_window, *self.size, 0, 0, "old-fw 0.1")
        return self._send(P.T_HELLO, P.hello_payload(h))


def test_protocol_mismatch_is_refused_and_no_frames_are_sent(tmp_path):
    r = Rig(tmp_path, panel=OldFirmware())
    r.run(10)
    st = r.st
    assert st["state"] == "refused" and not st["connected"]
    assert "protocol v2" in st["last_error"] and "update the firmware" in st["last_error"]
    assert st["fw_version"] == "old-fw 0.1" and st["protocol_version"] == 2
    assert [f.type for f in r.link.written] == [P.T_HELLO]  # only our HELLO request
    assert r.panel.refreshes == [] and r.panel.mode == P.MODE_PANEL
    assert r.links[0].closed


def test_refused_panel_is_retried_every_60s_and_recovers_after_a_firmware_update(tmp_path):
    r = Rig(tmp_path, panel=OldFirmware())
    r.run(59)
    assert len(r.links) == 1
    r.run(3)
    assert len(r.links) == 2 and r.st["state"] == "refused"
    assert r.connect_times[1] - r.connect_times[0] == pytest.approx(60, abs=0.2)


def test_refused_panel_recovers_when_firmware_is_fixed(tmp_path):
    panel = OldFirmware()
    r = Rig(tmp_path, panel=panel)
    r.run(5)
    assert r.st["state"] == "refused"
    OldFirmware.ver = 1
    try:
        r.run(65)
    finally:
        OldFirmware.ver = 2
    assert r.st["connected"] and r.st["last_frame_at"] is not None
    assert r.panel.glass == bytearray(r.expected("a"))


def test_wrong_panel_size_is_refused(tmp_path):
    class Small(OldFirmware):
        ver, size = 1, (400, 300)

    r = Rig(tmp_path, panel=Small())
    r.run(3)
    assert r.st["state"] == "refused" and "400x300" in r.st["last_error"]
    assert [f.type for f in r.link.written] == [P.T_HELLO]


def test_short_hello_is_a_connection_error_not_a_crash(rig):
    rig.run(0.2)
    rig.link._out.append((0, P.encode(P.T_HELLO, 0, b"\x01\x00")))
    rig.run(0.5)
    assert rig.st["state"] == "disconnected" and "short HELLO" in rig.st["last_error"]


# ---------------------------------------------------------------- NAK handling

class FlakyPanel(EmulatedPanel):
    """NAKs the first `n` RECTs with `reason`."""

    def __init__(self, n=1, reason=P.NAK_BAD_RECT, **kw):
        super().__init__(**kw)
        self.n, self.reason = n, reason

    def _rect(self, f):
        if self.n > 0:
            self.n -= 1
            return self._nak(f.seq, self.reason)
        return super()._rect(f)


def test_single_nak_resyncs_with_a_full_redraw(tmp_path):
    r = Rig(tmp_path, panel=FlakyPanel(n=1))
    r.run(4)
    st = r.st
    assert st["naks"] == 1 and "NAK BAD_RECT for rect" in st["last_error"]
    assert st["connected"]
    assert r.panel.glass == bytearray(r.expected("a"))
    assert refresh_kinds(r) == "F"  # the batch with the NAK was abandoned, the redraw succeeded once
    assert st["full_refreshes"] == 1


def test_wrong_mode_nak_makes_the_engine_take_the_display_again(rig):
    rig.run(2)
    rig.panel.mode = P.MODE_PANEL  # panel went back to its own screens behind our back
    rig.data.d["system.cpu.load"] = 64.0
    rig.run(5)
    assert rig.st["naks"] >= 1 and "WRONG_MODE" in rig.st["last_error"]
    assert rig.panel.mode == P.MODE_HOST
    assert rig.panel.glass == bytearray(rig.expected("a"))


def test_nak_on_refresh_is_handled(tmp_path):
    class NoRefresh(EmulatedPanel):
        n = 1

        def _refresh(self, f):
            if self.n:
                self.n -= 1
                return self._nak(f.seq, P.NAK_BAD_ARG)
            return super()._refresh(f)

    r = Rig(tmp_path, panel=NoRefresh())
    r.run(4)
    assert "NAK BAD_ARG for refresh" in r.st["last_error"] and r.panel.glass == bytearray(r.expected("a"))


def test_persistent_nak_gives_up_after_three_and_reconnects_with_backoff(tmp_path):
    r = Rig(tmp_path, panel=FlakyPanel(n=10**6))
    r.run(1.5)
    assert r.st["naks"] == 3 and r.st["state"] == "disconnected"
    assert "rejected 3 frames in a row" in r.st["last_error"]
    r.run(30)
    assert len(r.links) >= 2 and r.connect_times[1] - r.connect_times[0] >= 2
    assert r.panel.refreshes == []  # nothing ever reached the glass


def test_unknown_nak_reason_is_still_reported(rig):
    rig.run(2)
    rig.data.d["system.cpu.load"] = 99.0
    rig.link.mute = True  # the panel's own ACKs are swallowed; we inject the reply
    while not rig.engine._inflight:  # step until the RECT is on the wire
        rig.run(0.05)
    seq = next(iter(rig.engine._inflight))
    rig.link._out.append((0, P.encode(P.T_NAK, 0, bytes([seq, 99]))))
    rig.run(0.1)
    assert rig.st["naks"] == 1 and "NAK 99 for" in rig.st["last_error"]


def test_stray_acks_naks_and_logs_are_ignored(rig):
    rig.run(2)
    out = rig.link._out
    out.append((0, P.encode(P.T_ACK, 0, bytes([200]))))
    out.append((0, P.encode(P.T_NAK, 0, bytes([201, 3]))))
    out.append((0, P.encode(P.T_ACK, 0, b"")))
    out.append((0, P.encode(P.T_NAK, 0, b"\x05")))
    out.append((0, P.encode(P.T_LOG, 0, b"panel says hi\n")))
    out.append((0, b"\x00garbage\xa5"))
    rig.run(1)
    assert rig.st["naks"] == 0 and rig.st["connected"] and rig.st["last_error"] is None


def test_unsolicited_hello_means_panel_reset_and_triggers_full_redraw(rig):
    rig.run(2)
    rig.panel.alive = False  # panel rebooted: forgets the session, back in its own mode, glass unknown
    rig.panel.mode = P.MODE_PANEL
    rig.data.d["system.cpu.load"] = 5.0
    rig.run(8)
    assert rig.st["connected"] and rig.panel.mode == P.MODE_HOST
    assert rig.panel.glass == bytearray(rig.expected("a"))
    assert refresh_kinds(rig) == "FF"


def test_ack_timeout_drops_the_connection(rig):
    rig.run(2)
    rig.link.mute = True  # panel hangs
    rig.run(5.5)  # heartbeat goes out at ~5 s and is never ACKed
    assert rig.st["state"] == "ready" and rig.st["last_error"] is None
    rig.run(5)
    assert "did not answer heartbeat" in rig.st["last_error"] and rig.st["state"] != "ready"
    assert rig.links[0].closed


def test_successful_reconnect_clears_the_old_error(rig):
    rig.run(2)
    rig.link.mute = True
    rig.run(10)
    assert "did not answer heartbeat" in rig.st["last_error"] and rig.st["last_error_at"] is not None
    rig.link.mute = False
    rig.run(10)
    st = rig.st
    assert st["state"] == "ready" and st["reconnects"] == 1
    assert st["last_error"] is None and st["last_error_at"] is None


def test_error_stays_while_reconnects_keep_failing(rig):
    rig.run(2)
    rig.link.unplug()
    rig.plugged = False
    rig.run(0.2)
    first = rig.st["last_error_at"]
    assert "disconnected" in rig.st["last_error"]
    rig.run(30, dt=0.25)   # several failed retries
    st = rig.st
    assert st["state"] == "disconnected" and "no panel found" in st["last_error"] and st["last_error_at"] >= first


def test_new_error_after_recovery_is_shown(rig):
    rig.run(2)
    rig.link.unplug()
    rig.run(8)
    assert rig.st["connected"] and rig.st["last_error"] is None
    rig.plugged = False
    rig.link.mute = True
    rig.run(12)
    assert "did not answer heartbeat" in rig.st["last_error"] or "no panel found" in rig.st["last_error"]
    assert rig.st["last_error_at"] is not None and not rig.st["connected"]


def test_nak_after_a_reconnect_still_shows(tmp_path):
    r = Rig(tmp_path, panel=FlakyPanel(n=0))
    r.run(2)
    r.link.unplug()
    r.run(8)
    assert r.st["connected"] and r.st["last_error"] is None
    r.panel.n = 1   # the next RECT is refused while connected
    r.data.d["system.cpu.load"] = 77.0
    r.run(4)
    assert r.st["connected"] and "NAK BAD_RECT for rect" in r.st["last_error"]


def test_refresh_timeout_is_15s(tmp_path):
    r = Rig(tmp_path, partial_s=60.0, full_s=60.0)  # a refresh that never ends
    r.run(14)
    assert r.st["state"] == "ready"
    r.run(3)
    assert "did not answer refresh" in r.st["last_error"]


# ---------------------------------------------------------------- rate control / coalescing

def test_refresh_is_never_queued_and_changes_are_coalesced(tmp_path):
    r = Rig(tmp_path, Settings(rotation=["a"], ghost_clear_minutes=0), partial_s=1.8, full_s=4.1)
    r.run(6)  # first full draw (4.1 s) finishes
    assert r.panel.refreshes and r.panel.refreshes[0][0] == "F"
    for i in range(190):  # 19 s: the value changes every 0.1 s, the screen renders every 1 s
        r.data.d["system.cpu.load"] = float(i % 100)
        r.run(0.1)
    r.run(4)
    link = r.link
    assert link.max_pending_refresh <= 1
    assert link.max_backlog <= 8192  # never more than rx_window unacknowledged bytes
    partials = r.st["partial_refreshes"]
    elapsed = r.clock.t - 1006  # data starts changing at t+6
    assert elapsed / 1.8 - 3 <= partials <= elapsed / 1.8 + 1  # one per refresh time, not per change (190) or per render (~23)
    assert r.st["coalesced"] >= partials - 1
    r.data.d["system.cpu.load"] = 42.0
    r.run(6)
    assert r.panel.glass == bytearray(r.expected("a"))  # latest data won


def test_next_render_happens_right_after_the_refresh_ack(tmp_path):
    r = Rig(tmp_path, Settings(rotation=["a"]), partial_s=1.8, full_s=1.8)
    r.run(3)
    r.data.d["system.cpu.load"] = 11.0
    r.run(1.2)  # render at ~t+1 starts refresh (1.8 s)
    r.data.d["system.cpu.load"] = 22.0  # arrives while refreshing
    t_ack = None
    n = r.st["partial_refreshes"]
    while r.clock.t < 1000 + 20:
        r.run(0.05)
        if t_ack is None and r.st["partial_refreshes"] > n:
            t_ack = r.clock.t
        if t_ack and len(r.panel.refreshes) >= 4:
            break
    assert r.panel.glass == bytearray(r.expected("a"))
    assert r.link.max_pending_refresh <= 1


def test_window_limits_bytes_in_flight(tmp_path):
    r = Rig(tmp_path, panel=EmulatedPanel(rx_window=4200))
    r.run(4)
    assert r.link.max_backlog <= 4200
    assert r.panel.glass == bytearray(r.expected("a"))


def test_zero_rx_window_still_makes_progress(tmp_path):
    r = Rig(tmp_path, panel=EmulatedPanel(rx_window=0))
    r.run(5)
    assert r.panel.glass == bytearray(r.expected("a"))


# ---------------------------------------------------------------- now playing, settings, screens

def test_now_playing_switches_and_returns(rig):
    rig.run(2)
    rig.engine.set_now_playing(570)
    rig.run(3)
    assert rig.st["current_screen"] == "now-playing" and rig.st["now_playing"] == 570
    assert rig.panel.glass == bytearray(rig.expected("now-playing"))
    assert rig.data.np == [570]
    rig.engine.set_now_playing(None)
    rig.run(3)
    assert rig.st["current_screen"] == "a" and rig.st["now_playing"] is None
    assert rig.panel.glass == bytearray(rig.expected("a"))
    assert rig.data.np == [570, None]
    assert refresh_kinds(rig) == "FFF"


def test_bad_appid_is_rejected_and_changes_nothing(rig):
    rig.run(2)
    with pytest.raises(ValueError):
        rig.engine.set_now_playing("bad")
    rig.run(2)
    assert rig.st["current_screen"] == "a" and rig.st["now_playing"] is None


def test_now_playing_disabled_in_settings(tmp_path):
    r = Rig(tmp_path, Settings(rotation=["a"], auto_now_playing=False))
    r.run(2)
    r.engine.set_now_playing(570)
    r.run(3)
    assert r.st["current_screen"] == "a"


def test_pin_wins_over_now_playing(tmp_path):
    r = Rig(tmp_path, Settings(rotation=["a"], pinned="b"))
    r.run(2)
    r.engine.set_now_playing(570)
    r.run(3)
    assert r.st["current_screen"] == "b" and r.st["pinned"] == "b"


def test_registry_without_set_now_playing_still_switches(tmp_path):
    class Plain:
        def snapshot(self):
            return {}

    r = Rig(tmp_path)
    r.engine.registry = Plain()
    r.run(2)
    r.engine.set_now_playing(1)
    r.run(2)
    assert r.st["current_screen"] == "now-playing"


def test_per_screen_settings_are_applied(tmp_path):
    root = tmp_path / "s"
    write_screen(root, "cfg", "{settings.word}", settings=[{"key": "word", "type": "string", "default": "x"}])
    write_screen(root, "a", "A")
    screens, errs = load_screens(root)
    clock, data = Clock(), Data()
    link = EmulatedLink(clock=clock)
    e = Engine(data, screens, Settings(rotation=["cfg"], screen_settings={"cfg": {"word": "hello"}}), connect=lambda: link, clock=clock)
    for _ in range(60):
        e.step()
        clock.t += 0.05
    from inkterface_core.screens import render_frame
    assert link.panel.glass == bytearray(render_frame(screens["cfg"], data.snapshot(), {"word": "hello"}).translate(INVERT))
    assert link.panel.glass != bytearray(render_frame(screens["cfg"], data.snapshot(), None).translate(INVERT))


def test_invalid_per_screen_settings_fall_back_to_defaults_with_an_error(tmp_path):
    r = Rig(tmp_path, Settings(rotation=["a"], screen_settings={"a": {"nonexistent": 1}}))
    r.run(3)
    assert "settings for a" in r.st["last_error"]
    assert r.panel.glass == bytearray(r.expected("a"))


def test_update_settings_at_runtime(rig):
    rig.run(2)
    rig.engine.update_settings(Settings(rotation=["b"], rotation_interval_s=30))
    rig.run(3)
    assert rig.st["current_screen"] == "b" and rig.panel.glass == bytearray(rig.expected("b"))


def test_set_screens_removing_the_current_one_switches(rig):
    rig.run(2)
    rig.engine.set_screens({k: v for k, v in rig.screens.items() if k != "a"})
    rig.run(3)
    assert rig.st["current_screen"] == "b"


def test_no_screens_is_an_error_not_a_crash(tmp_path):
    r = Rig(tmp_path)
    r.engine.set_screens({})
    r.run(3)
    assert r.st["last_error"] == "no active screens available" and r.st["connected"]
    assert r.panel.refreshes == []
    r.engine.set_screens(r.screens)
    r.run(3)
    assert r.panel.glass == bytearray(r.expected("a"))


def test_settings_error_is_surfaced_in_status(tmp_path):
    r = Rig(tmp_path, settings_error="bad file; using defaults")
    assert r.st["settings_error"] == "bad file; using defaults"


# ---------------------------------------------------------------- failures inside rendering

def test_render_exception_does_not_kill_the_engine(rig, monkeypatch):
    rig.run(2)
    monkeypatch.setattr(engine_mod, "render_frame", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    rig.run(3)
    assert "render a: RuntimeError: boom" in rig.st["last_error"] and rig.st["connected"]
    monkeypatch.undo()
    rig.data.d["system.cpu.load"] = 3.0
    rig.run(3)
    assert rig.panel.glass == bytearray(rig.expected("a"))


def test_registry_failure_is_reported_and_retried(rig):
    rig.run(2)
    rig.data.fail = RuntimeError("sources down")
    rig.run(3)
    assert "sources down" in rig.st["last_error"] and rig.st["connected"]
    rig.data.fail = None
    rig.data.d["system.cpu.load"] = 1.0
    rig.run(3)
    assert rig.panel.glass == bytearray(rig.expected("a"))


# ---------------------------------------------------------------- run loop, thread, stop

def test_run_loop_with_fake_sleep_and_survives_a_bug(tmp_path):
    r = Rig(tmp_path)
    calls = {"n": 0}
    real = r._connect

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("driver bug")
        return real()

    def sleep(s):
        r.clock.t += s
        if r.clock.t > 1020:
            r.engine._stop.set()

    r.engine._connect, r.engine._sleep = flaky, sleep
    r.engine.run()
    assert "driver bug" in (r.engine._status.last_error or "") or r.st["state"] == "stopped"
    assert r.panel.refreshes and r.st["state"] == "stopped"
    assert r.link.written[-1].type == P.T_MODE and r.link.written[-1].payload == bytes([P.MODE_PANEL])


def test_stop_hands_the_panel_back_to_its_own_screens(rig):
    rig.run(2)
    rig.engine.stop()
    assert rig.panel.mode == P.MODE_PANEL and rig.st["state"] == "stopped" and rig.link.closed
    rig.run(1)  # stepping a stopped engine does nothing
    assert rig.st["state"] == "stopped"


def test_stop_without_connection(rig):
    rig.engine.stop()
    assert rig.st["state"] == "stopped" and rig.links == []


def test_stop_when_the_link_is_dead(rig):
    rig.run(2)
    rig.link.unplug()
    rig.engine.stop()
    assert rig.st["state"] == "stopped"


def test_stop_survives_close_errors(rig):
    rig.run(2)

    def bad_close():
        raise RuntimeError("close failed")

    rig.link.close = bad_close
    rig.engine.stop()
    assert rig.st["state"] == "stopped"


def test_drop_survives_close_errors(rig):
    rig.run(2)
    rig.link.close = lambda: (_ for _ in ()).throw(RuntimeError("x"))
    rig.link.unplug()
    rig.run(0.2)
    assert rig.st["state"] == "disconnected"


def test_thread_start_stop_with_real_clock(tmp_path):
    screens = make_screens(tmp_path / "s")
    link = EmulatedLink()
    e = Engine(Data(), screens, Settings(rotation=["a"]), connect=lambda: link)
    e.start()
    e.start()  # idempotent
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not link.panel.refreshes:
        time.sleep(0.02)
        e.status()
    assert link.panel.refreshes
    e.stop()
    assert e.status()["state"] == "stopped" and link.panel.mode == P.MODE_PANEL
    e.start()  # can be restarted after stop
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and e.status()["state"] != "ready":
        time.sleep(0.02)
    e.stop()


# ---------------------------------------------------------------- helpers

def test_usb_connect_uses_vid_discovery_and_dtr_rts_safe_open():
    seen = {}
    port = object()

    def find(serial):
        seen["serial"] = serial
        return ["/dev/ttyACM7", "/dev/ttyACM9"]

    assert usb_connect("ABC", find=find, open_port=lambda p: (seen.setdefault("path", p), port)[1]) is port
    assert seen == {"serial": "ABC", "path": "/dev/ttyACM7"}


def test_usb_connect_without_a_panel_is_file_not_found():
    with pytest.raises(FileNotFoundError, match="VID 303a"):
        usb_connect(find=lambda s: [])


def test_usb_connect_permission_denied_says_how_to_fix_it():
    def deny(path):
        raise PermissionError(13, "Permission denied")

    with pytest.raises(PermissionError, match=r"/dev/ttyACM7.*deck.*Serial permission") as ei:
        usb_connect(find=lambda s: ["/dev/ttyACM7"], open_port=deny)
    assert ei.value.errno == 13


def test_usb_connect_defaults_are_the_real_helpers():
    from inkterface_core import usb_link
    assert usb_connect.__defaults__ == (None, usb_link.find_panels, usb_link.open_port)


def test_load_screens(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    write_screen(a, "one", "1")
    write_screen(a, "idle-only", "x", active=False)
    (a / "broken").mkdir()
    (a / "broken" / "screen.json").write_text("{bad")
    (a / "not-a-package").mkdir()
    write_screen(b, "one", "override")
    screens, errs = load_screens(a, b, tmp_path / "missing")
    assert sorted(screens) == ["one"] and screens["one"].root == b / "one"
    assert len(errs) == 1 and errs[0].startswith("broken:")


def test_load_screens_reports_extra_error_count(tmp_path):
    d = tmp_path / "x" / "bad"
    d.mkdir(parents=True)
    (d / "screen.json").write_text('{"schema": 1}')
    _, errs = load_screens(tmp_path / "x")
    assert "more)" in errs[0]


def test_status_has_every_documented_field(rig):
    rig.run(1)
    assert set(rig.st) >= {"connected", "state", "fw_version", "protocol_version", "panel_size", "current_screen", "pinned",
                           "now_playing", "last_error", "last_error_at", "last_frame_at", "retry_in_s", "frames_rendered",
                           "unchanged_ticks", "coalesced", "rects_sent", "bytes_sent", "heartbeats_sent", "partial_refreshes",
                           "full_refreshes", "naks", "reconnects", "settings_error", "screen_errors"}
    assert rig.st["screen_errors"] == []
    rig.engine.set_screens(rig.screens, ["broken: bad json"])
    assert rig.st["screen_errors"] == ["broken: bad json"]
    rig.st["screen_errors"].append("mutation must not leak")
    assert rig.st["screen_errors"] == ["broken: bad json"]


def test_constructor_takes_initial_screen_errors(tmp_path):
    r = Rig(tmp_path, screen_errors=["x: y"])
    assert r.st["screen_errors"] == ["x: y"]


def test_real_screens_end_to_end(tmp_path):
    """The four built-in screens through the whole pipeline with canned data."""
    screens, errs = load_screens(Path(__file__).resolve().parents[2] / "screens")
    assert not errs and len(screens) == 4
    clock = Clock()
    data = Data(**{"time.now": "2026-11-10T12:34:56+01:00"})
    link = EmulatedLink(clock=clock)
    e = Engine(data, screens, Settings(rotation=sorted(screens), rotation_interval_s=10), connect=lambda: link, clock=clock)
    seen = []
    for _ in range(int(45 / 0.05)):
        e.step()
        cur = e.status()["current_screen"]
        if cur and cur not in seen:
            seen.append(cur)
        clock.t += 0.05
    assert seen == sorted(screens)[:4] and len(seen) == 4
    assert e.status()["full_refreshes"] >= 3 and e.status()["last_error"] is None


def test_stop_during_handshake_just_closes(tmp_path):
    class Deaf(EmulatedPanel):
        def feed(self, data):
            return b""

    r = Rig(tmp_path, panel=Deaf())
    r.run(2)
    assert r.st["state"] == "handshake"
    r.engine.stop()
    assert r.st["state"] == "stopped" and r.link.closed
    assert [f.type for f in r.link.written] == [P.T_HELLO]  # no MODE PANEL: we never owned the display


def test_frames_after_a_refused_hello_in_the_same_read_are_ignored(tmp_path):
    class Chatty(OldFirmware):
        def feed(self, data):
            return super().feed(data) + P.encode(P.T_ACK, 0, b"\x00") + P.encode(P.T_NAK, 1, b"\x00\x03")

    r = Rig(tmp_path, panel=Chatty())
    r.run(1)
    assert r.st["state"] == "refused" and r.st["naks"] == 0


def test_real_registry_feeds_the_clock_screen(tmp_path):
    """Real sources -> engine -> panel: the Big Clock must show digits, not '--' (sources emit datetime objects)."""
    from inkterface_core.screens import render_frame
    from inkterface_core.sources import default_registry
    screens, _ = load_screens(Path(__file__).resolve().parents[2] / "screens")
    reg = default_registry(network=False)
    reg.poll_due(force=True)
    clock = Clock()
    link = EmulatedLink(clock=clock)
    e = Engine(reg, screens, Settings(rotation=["big-clock"]), connect=lambda: link, clock=clock)
    for _ in range(60):
        e.step()
        clock.t += 0.05
    placeholder = render_frame(screens["big-clock"], {}, None).translate(INVERT)
    shown = bytes(link.panel.glass)
    top = slice(0, 30 * 81 + 200 * 81)  # the clock widget rows
    assert shown[top] != placeholder[top]


# -- hooks used by the Decky plugin ---------------------------------------------------------
def test_render_hook_replaces_the_default_renderer(tmp_path):
    calls = []

    def hook(screen, snap, over):
        calls.append((screen.id, over))
        return render_frame(screen, snap, over)

    r = Rig(tmp_path, render=hook)
    r.run(2)
    assert calls and calls[0][0] == "a" and r.panel.glass == bytearray(r.expected("a"))


def test_render_hook_gets_the_bad_settings_fallback_too(tmp_path):
    seen = []

    def hook(screen, snap, over):
        seen.append(over)
        return render_frame(screen, snap, over)

    r = Rig(tmp_path, settings=Settings(rotation=["a"], screen_settings={"a": {"zzz": 1}}), render=hook)
    r.run(2)
    assert seen[0] == {"zzz": 1} and seen[1] is None and "settings for a" in r.st["last_error"]


def test_set_registry_switches_data_and_forwards_the_running_game(tmp_path):
    r = Rig(tmp_path)
    r.run(2)
    r.engine.set_now_playing(570)
    new = Data(**{"system.cpu.load": 77.0})
    r.engine.set_registry(new)
    assert new.np == [570]
    r.run(3)
    assert r.panel.glass == bytearray(render_frame(r.screens["now-playing"], new.snapshot(), None).translate(INVERT))


def test_set_registry_without_now_playing_support_is_fine(tmp_path):
    class Bare:
        def snapshot(self):
            return {"system.cpu.load": 1.0, "system.gpu.load": 2.0}

    r = Rig(tmp_path)
    r.run(1)
    r.engine.set_now_playing(570)
    r.engine.set_registry(Bare())
    r.run(2)
    assert r.st["connected"] and r.st["last_error"] is None


def test_hello_capabilities_reach_the_status(tmp_path):
    r = Rig(tmp_path, panel=EmulatedPanel(caps=3, idle_free=123456))
    assert r.st["caps"] is None
    r.run(2)
    assert (r.st["caps"], r.st["idle_flash_free"]) == (3, 123456)
