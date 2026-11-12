"""Event-driven rendering: a change notification renders at once, refresh_s is only the max-staleness cap."""
from engine_testlib import Data, Rig

from inkterface_core import proto as P
from inkterface_core.engine import Settings
from inkterface_core.engine import engine as engine_mod

NP = Settings(rotation=["now-playing"], rotation_interval_s=30, ghost_clear_minutes=0)   # refresh_s 5: the old latency floor
CPU = "system__cpu__load"


def settled(tmp_path, settings=NP, **kw):
    r = Rig(tmp_path, settings, **kw)
    r.run(8)   # first full frame done, nothing pending
    return r




class Stamp:
    """Records the fake-clock time every REFRESH is first seen on the wire."""

    def __init__(self, r):
        self.r, self.t = r, []
        self.n = len(r.frames(P.T_REFRESH))

    def poll(self):
        k = len(self.r.frames(P.T_REFRESH))
        self.t += [self.r.clock.t] * (k - self.n)
        self.n = k


def run_stamped(r, seconds, stamp, dt=0.01):
    for _ in range(round(seconds / dt)):
        r.engine.step()
        stamp.poll()
        r.clock.t += dt


# ---------------------------------------------------------------- change -> render now

def test_change_renders_at_once_not_after_refresh_s(tmp_path):
    r = settled(tmp_path)
    r.run(2.3)   # mid refresh_s window: the next scheduled render is up to 5 s away
    st0, t0 = r.st, r.clock.t
    stamp = Stamp(r)
    r.data.change(**{CPU: 77.0})
    run_stamped(r, 1.0, stamp)
    assert len(stamp.t) == 1 and stamp.t[0] - t0 < 0.1   # one engine tick, not 2-5 s
    assert r.panel.glass == bytearray(r.expected("now-playing"))
    assert r.st["partial_refreshes"] == st0["partial_refreshes"] + 1


def test_notification_without_a_data_change_renders_but_sends_nothing(tmp_path):
    r = settled(tmp_path)
    st0, n = r.st, len(r.link.written)
    r.data.change(**{CPU: r.data.d["system.cpu.load"]})
    r.run(0.5)
    assert r.st["frames_rendered"] == st0["frames_rendered"] + 1
    assert r.st["unchanged_ticks"] == st0["unchanged_ticks"] + 1
    assert [f.type for f in r.link.written[n:]] == []


def test_no_notification_means_no_render_until_the_staleness_cap(tmp_path):
    r = settled(tmp_path)
    st0 = r.st
    r.data.d["system.cpu.load"] = 55.0   # changed silently (a source without events)
    r.run(2.0)
    assert r.st["frames_rendered"] == st0["frames_rendered"]   # nothing rendered yet
    r.run(4.0)                                                 # refresh_s (5 s) elapsed: the cap fires
    assert r.st["frames_rendered"] == st0["frames_rendered"] + 1
    assert r.panel.glass == bytearray(r.expected("now-playing"))


def test_max_staleness_is_refresh_s_of_the_current_screen(tmp_path):
    r = settled(tmp_path)
    st0 = r.st
    r.run(20)
    assert 3 <= r.st["frames_rendered"] - st0["frames_rendered"] <= 5   # one per 5 s, none extra


def test_a_one_second_screen_still_renders_every_second(tmp_path):
    r = Rig(tmp_path, Settings(rotation=["a"], ghost_clear_minutes=0))
    r.run(3)
    st0 = r.st
    r.run(10)
    assert 9 <= r.st["frames_rendered"] - st0["frames_rendered"] <= 11


# ---------------------------------------------------------------- coalescing

def test_changes_during_a_slow_refresh_give_exactly_one_follow_up(tmp_path):
    r = Rig(tmp_path, NP, partial_s=0.7, full_s=4.0)
    r.run(8)
    r.data.change(**{CPU: 11.0})
    r.run(0.2)                                  # refresh #1 in flight (0.7 s)
    st0, shown = r.st, bytes(r.panel.glass)
    for v in (22.0, 33.0, 44.0, 55.0, 66.0):   # five more changes while the panel is busy
        r.data.change(**{CPU: v})
        r.run(0.05)
    assert r.st["partial_refreshes"] == st0["partial_refreshes"]   # still waiting for the ACK
    assert r.link.max_pending_refresh <= 1
    r.run(3.0)
    assert r.st["partial_refreshes"] == st0["partial_refreshes"] + 2   # #1 and ONE follow-up, not five
    assert r.st["coalesced"] >= 1
    assert r.panel.glass == bytearray(r.expected("now-playing")) and bytes(r.panel.glass) != shown   # latest value won


def test_burst_before_one_tick_is_one_refresh(tmp_path):
    r = settled(tmp_path)
    st0 = r.st
    for v in range(10):
        r.data.change(**{CPU: float(v * 9)})
    r.run(1.0)
    assert r.st["frames_rendered"] == st0["frames_rendered"] + 1
    assert r.st["partial_refreshes"] == st0["partial_refreshes"] + 1


def test_follow_up_starts_right_after_the_ack(tmp_path):
    r = Rig(tmp_path, NP, partial_s=0.7, full_s=4.0)
    r.run(8)
    stamp = Stamp(r)
    r.data.change(**{CPU: 11.0})
    run_stamped(r, 0.3, stamp)
    r.data.change(**{CPU: 99.0})
    run_stamped(r, 3.0, stamp)
    assert len(stamp.t) == 2
    assert 0.65 <= stamp.t[1] - stamp.t[0] <= 0.85   # panel speed, not refresh_s (5 s)


# ---------------------------------------------------------------- sample when the panel gets free

class Sampling(Data):
    """A registry whose fast source reports a change only when polled: sample_now() is what finds it."""

    def __init__(self):
        super().__init__()
        self.pending, self.calls, self.boom = None, 0, False

    def sample_now(self):
        self.calls += 1
        if self.boom:
            raise RuntimeError("sample_now exploded")
        if self.pending is not None:
            v, self.pending = self.pending, None
            self.change(**{CPU: v})


def sampling_rig(tmp_path, **kw):
    r = Rig(tmp_path, NP, partial_s=0.7, full_s=4.0, **kw)
    new = Sampling()
    r.engine.set_registry(new)
    r.data = new
    r.run(8)
    return r


def test_a_change_found_only_by_sampling_is_rendered_right_after_the_ack(tmp_path):
    r = sampling_rig(tmp_path)
    r.data.change(**{CPU: 11.0})
    r.run(0.5)                                      # refresh #1 in flight, 0.2 s left
    r.data.d["system.cpu.load"] = 55.0              # the source has a new value; nobody has polled it yet
    r.data.pending = 55.0
    st0 = r.st
    r.run(0.5)                                      # ACK at ~0.7 s, then the sample and the follow-up render
    assert r.st["partial_refreshes"] == st0["partial_refreshes"] + 1
    r.run(1.0)
    assert r.st["partial_refreshes"] == st0["partial_refreshes"] + 2   # the follow-up did not wait for refresh_s (5 s)
    assert r.panel.glass == bytearray(r.expected("now-playing"))


def test_sampling_happens_once_per_acknowledged_refresh_and_never_while_idle(tmp_path):
    r = sampling_rig(tmp_path)
    r.run(2.0)
    n = r.data.calls
    r.run(3.0)                                      # idle: no refresh, no ACK
    assert r.data.calls == n
    for v in (21.0, 22.0, 23.0):
        r.data.change(**{CPU: v})
        r.run(1.5)                                  # each change is one refresh and one ACK
    assert r.data.calls == n + 3


def test_sampling_that_finds_nothing_renders_nothing(tmp_path):
    r = sampling_rig(tmp_path)
    r.data.change(**{CPU: 31.0})
    r.run(0.8)
    st0 = r.st
    r.run(1.0)
    assert r.st["frames_rendered"] == st0["frames_rendered"]


def test_a_failing_sample_now_does_not_stop_the_engine(tmp_path):
    r = sampling_rig(tmp_path)
    r.data.boom = True
    r.data.change(**{CPU: 41.0})
    r.run(1.5)
    r.data.change(**{CPU: 42.0})
    r.run(1.5)
    assert r.st["connected"] and r.panel.glass == bytearray(r.expected("now-playing"))


# ---------------------------------------------------------------- no busy loop

def test_continuous_notifications_cannot_spin_the_renderer(tmp_path):
    r = settled(tmp_path)
    st0 = r.st
    for i in range(1000):   # a notification on every 10 ms step for 10 s, data never actually changes
        r.data.change()
        r.run(0.01, dt=0.01)
    renders = r.st["frames_rendered"] - st0["frames_rendered"]
    assert renders <= 10 / engine_mod.MIN_RENDER_GAP_S + 2


def test_idle_engine_does_not_render_between_staleness_ticks(tmp_path):
    r = settled(tmp_path)
    st0 = r.st
    r.run(1.5)   # the next staleness tick is ~2 s away
    assert r.st["frames_rendered"] == st0["frames_rendered"]


def test_dirty_flag_is_consumed_by_the_render(tmp_path):
    r = settled(tmp_path)
    r.data.change(**{CPU: 31.0})
    r.run(1.0)
    n = r.st["frames_rendered"]
    r.run(3.0)
    assert r.st["frames_rendered"] == n


# ---------------------------------------------------------------- connection states

def test_notification_before_the_panel_is_ready_is_harmless(tmp_path):
    r = Rig(tmp_path, NP)
    r.data.change(**{CPU: 5.0})
    r.run(3)
    assert r.st["connected"] and r.panel.glass == bytearray(r.expected("now-playing"))


def test_notifications_while_paused_do_not_render(tmp_path):
    r = settled(tmp_path)
    r.engine.pause()
    n = r.st["frames_rendered"]
    for _ in range(20):
        r.data.change(**{CPU: 1.0})
        r.run(0.1)
    assert r.st["frames_rendered"] == n and r.st["state"] == "paused"


def test_set_now_playing_acts_at_once(tmp_path):
    r = Rig(tmp_path, Settings(rotation=["a"], ghost_clear_minutes=0), full_s=0.0)
    r.run(3)
    r.run(0.35)   # not on a refresh_s boundary
    stamp = Stamp(r)
    t0 = r.clock.t
    r.engine.set_now_playing(570)
    run_stamped(r, 1.0, stamp)
    assert stamp.t and stamp.t[0] - t0 < 0.1
    assert r.st["current_screen"] == "now-playing" and r.data.np == [570]


# ---------------------------------------------------------------- watch / subscribe protocol

def test_engine_subscribes_and_watches_what_the_screen_binds(tmp_path):
    r = Rig(tmp_path, Settings(rotation=["a"]))
    assert r.data.listeners == [r.engine.notify_change]
    assert r.data.watched == [frozenset()]   # nothing before a panel is there
    r.run(3)
    assert r.data.watched[-1] == frozenset({"system.cpu.load", "system.gpu.load"})
    r.engine.stop()
    assert r.data.watched[-1] == frozenset()   # stopped: no fast sampling for nobody


def test_unplugging_drops_the_watch_and_reconnecting_restores_it(tmp_path):
    r = Rig(tmp_path, Settings(rotation=["a"]))
    r.run(3)
    r.link.unplug()
    r.plugged = False
    r.run(1.5)
    assert r.st["state"] == "disconnected" and r.data.watched[-1] == frozenset()
    r.plugged = True
    r.run(10)
    assert r.st["connected"] and r.data.watched[-1] == frozenset({"system.cpu.load", "system.gpu.load"})


def test_set_registry_moves_the_subscription(tmp_path):
    r = Rig(tmp_path, Settings(rotation=["a"]))
    r.run(3)
    new = Data(**{"system.cpu.load": 66.0})
    r.engine.set_registry(new)
    assert r.data.listeners == [] and new.listeners == [r.engine.notify_change]
    r.run(3)
    assert new.watched[-1] == frozenset({"system.cpu.load", "system.gpu.load"})
    n = r.st["frames_rendered"]
    new.change(**{CPU: 12.0})
    r.run(0.5)
    assert r.st["frames_rendered"] == n + 1


def test_registry_without_events_still_works(tmp_path):
    class Plain:
        def snapshot(self):
            return {"system.cpu.load": 1.0, "system.gpu.load": 2.0}

    r = Rig(tmp_path, Settings(rotation=["a"]))
    r.engine.set_registry(Plain())   # no subscribe/watch/unsubscribe: tick-driven as before
    r.run(3)
    assert r.st["connected"] and r.st["frames_rendered"] >= 2
