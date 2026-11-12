"""Registry change events: watch(), subscribe(), fast sampling for watched keys, history cadence, instant set_now_playing."""
import datetime
import threading
import time

import pytest
import sourcefs as F
from test_src_registry import Clock, Fake

from inkterface_core.sources import Registry, SourceError, TimeSource
from inkterface_core.sources.base import touches
from inkterface_core.sources.system import DISK_TTL_S, HWMON_TTL_S, SystemSource


def rig(*sources, **kw):
    c = Clock()
    r = Registry(sources, clock=c, **kw)
    hits = []
    r.subscribe(lambda: hits.append(c.t))
    return r, c, hits


@pytest.mark.parametrize("a,b,want", [
    ("system.cpu.load", "system.cpu.load", True),
    ("system.cpu.load", "system.cpu.load.history", True),
    ("steam.now_playing", "steam.now_playing.session_minutes", True),
    ("steam.now_playing.name", "steam.now_playing", True),
    ("steam.recent[0].name", "steam.recent", True),
    ("steam.recent", "steam.recent_other", False),
    ("system.cpu.load", "system.cpu.temp", False),
    ("system.cpu", "system.cpu2.load", False),
])
def test_touches(a, b, want):
    assert touches(a, b) is want and touches(b, a) is want


# ---------------------------------------------------------------- change events

def test_nothing_is_watched_by_default_so_nobody_is_told():
    s = Fake("s", ["a"])
    s.queue = [{"a": 1}]
    r, c, hits = rig(s)
    r.poll_due()
    assert hits == []


def test_a_changed_watched_key_notifies_once_per_poll():
    s = Fake("s", ["a", "b"])
    s.queue = [{"a": 1, "b": 1}, {"a": 2, "b": 2}]
    r, c, hits = rig(s)
    r.watch({"a"})
    r.poll_due()
    r.poll_due(force=True)
    assert len(hits) == 2


def test_unchanged_value_does_not_notify():
    s = Fake("s", ["a"])
    s.queue = [{"a": 1}, {"a": 1}, {"a": 1.0}]
    r, c, hits = rig(s)
    r.watch({"a"})
    for _ in range(3):
        r.poll_due(force=True)
    assert len(hits) == 1   # only the first poll (nothing known before); 1 == 1.0


def test_unwatched_key_change_does_not_notify():
    s = Fake("s", ["a", "b"])
    s.queue = [{"a": 1, "b": 1}, {"a": 1, "b": 2}]
    r, c, hits = rig(s)
    r.watch({"a"})
    r.poll_due(force=True)
    hits.clear()
    r.poll_due(force=True)
    assert hits == []


def test_watch_none_means_everything_and_empty_means_nothing():
    s = Fake("s", ["a"])
    s.queue = [{"a": 1}, {"a": 2}, {"a": 3}]
    r, c, hits = rig(s)
    r.watch(None)
    r.poll_due(force=True)
    assert len(hits) == 1
    r.watch([])
    r.poll_due(force=True)
    assert len(hits) == 1


def test_a_watched_child_key_sees_a_change_of_its_parent_dict():
    s = Fake("s", ["steam.now_playing"])
    s.queue = [{"steam.now_playing": {"name": "x", "session_minutes": 1}},
               {"steam.now_playing": {"name": "x", "session_minutes": 2}}]
    r, c, hits = rig(s)
    r.watch({"steam.now_playing.session_minutes"})
    r.poll_due(force=True)
    r.poll_due(force=True)
    assert len(hits) == 2


def test_history_key_is_watched_through_its_value():
    s = Fake("s", ["n"], hist=["n"])
    s.queue = [{"n": 1}, {"n": 2}]
    r, c, hits = rig(s)
    r.watch({"n.history"})
    r.poll_due(force=True)
    r.poll_due(force=True)
    assert len(hits) == 2


def test_failed_poll_does_not_notify_and_keeps_the_last_values():
    s = Fake("s", ["a"])
    s.queue = [{"a": 1}, SourceError("down")]
    r, c, hits = rig(s)
    r.watch({"a"})
    r.poll_due(force=True)
    r.poll_due(force=True)
    assert len(hits) == 1 and r.snapshot()["a"] == 1 and s.last_error == "down"


def test_a_raising_subscriber_does_not_stop_the_others_or_the_poll():
    s = Fake("s", ["a"])
    s.queue = [{"a": 1}]
    r, c, hits = rig(s)
    r.subscribe(lambda: 1 / 0)
    r.subscribe(lambda: hits.append("late"))
    r.watch({"a"})
    r.poll_due(force=True)
    assert hits[-1] == "late" and r.snapshot()["a"] == 1


def test_unsubscribe():
    s = Fake("s", ["a"])
    s.queue = [{"a": 1}]
    r, c, hits = rig(s)
    cb = lambda: hits.append("x")  # noqa: E731
    r.subscribe(cb)
    r.unsubscribe(cb)
    r.unsubscribe(cb)   # twice is fine
    r.watch({"a"})
    r.poll_due(force=True)
    assert "x" not in hits


# ---------------------------------------------------------------- fast sampling

def test_watched_source_with_fast_interval_is_due_sooner():
    s = Fake("s", ["a"], interval=10.0, fast_interval=0.5)
    r, c, hits = rig(s)
    r.watch({"a"})
    r.poll_due()
    assert r.poll_due() == []          # not yet
    c.t += 0.6
    assert r.poll_due() == ["s"]


def test_unwatched_or_unrelated_watch_keeps_the_normal_interval():
    s = Fake("s", ["a"], interval=10.0, fast_interval=0.5)
    r, c, hits = rig(s)
    r.watch({"other"})
    r.poll_due()
    c.t += 5
    assert r.poll_due() == []
    c.t += 5.1
    assert r.poll_due() == ["s"]


def test_source_without_fast_interval_ignores_watch():
    s = Fake("s", ["a"], interval=10.0)
    r, c, hits = rig(s)
    r.watch(None)
    r.poll_due()
    c.t += 1
    assert r.poll_due() == []


def test_watch_pulls_an_already_scheduled_poll_forward_and_unwatch_relaxes_it():
    s = Fake("s", ["a"], interval=10.0, fast_interval=0.5)
    r, c, hits = rig(s)
    r.poll_due()                       # next poll in 10 s
    c.t += 1
    r.watch({"a"})
    assert r.poll_due() == ["s"]       # was polled 1 s ago, fast interval 0.5 s: due now
    r.watch(frozenset())
    r.poll_due(force=True)             # the next poll goes back to the normal interval
    c.t += 5
    assert r.poll_due() == []


def test_fast_interval_never_slows_a_source_down():
    s = Fake("s", ["a"], interval=1.0, fast_interval=5.0)
    r, c, hits = rig(s)
    r.watch({"a"})
    r.poll_due()
    c.t += 1.01
    assert r.poll_due() == ["s"]


# ---------------------------------------------------------------- sample_now (the engine's panel-just-got-free poll)

def fast(**kw):
    return Fake("s", ["a"], interval=10.0, fast_interval=0.5, **kw)


def test_sample_now_polls_a_watched_fast_source_and_notifies_the_change():
    s = fast()
    s.queue = [{"a": 1}, {"a": 2}]
    r, c, hits = rig(s)
    r.watch({"a"})
    r.poll_due()
    hits.clear()
    c.t += 0.1                               # polled 0.1 s ago, due only in 0.4 s
    assert r.sample_now() == ["s"]
    assert len(hits) == 1 and r.snapshot()["a"] == 2


def test_sample_now_without_a_change_notifies_nobody():
    s = fast()
    s.queue = [{"a": 1}, {"a": 1}]
    r, c, hits = rig(s)
    r.watch({"a"})
    r.poll_due()
    hits.clear()
    c.t += 0.1
    assert r.sample_now() == ["s"] and hits == []


def test_sample_now_reschedules_so_the_poll_thread_does_not_poll_twice():
    s = fast()
    r, c, hits = rig(s)
    r.watch({"a"})
    r.poll_due()
    c.t += 0.3
    r.sample_now()
    c.t += 0.3                               # 0.6 s after the first poll, but only 0.3 s after sample_now
    assert r.poll_due() == []
    c.t += 0.25
    assert r.poll_due() == ["s"]


def test_sample_now_skips_what_it_must_not_poll():
    recent, unwatched, slow, never = fast(), Fake("u", ["u"], interval=10.0, fast_interval=0.5), Fake("n", ["n"], interval=10.0), fast()
    never.name, never.paths = "never", ("z",)
    r, c, hits = rig(recent, unwatched, slow)
    r.watch({"a", "n"})                      # "u" is not watched, "n" has no fast_interval
    r.poll_due()
    c.t += 0.01
    assert r.sample_now() == []              # all polled 10 ms ago: younger than min_age
    c.t += 0.1
    assert r.sample_now() == ["s"]           # only the watched fast source
    r.add(never)
    r.watch({"a", "z"})
    assert "never" not in r.sample_now()     # never polled yet: the poll threads own the first poll


def test_sample_now_with_nothing_watched_polls_nothing():
    s = fast()
    r, c, hits = rig(s)
    r.poll_due()
    c.t += 5
    assert r.sample_now() == []


def test_sample_now_survives_a_failing_source():
    s = fast()
    s.queue = [{"a": 1}, RuntimeError("boom")]
    r, c, hits = rig(s)
    r.watch({"a"})
    r.poll_due()
    c.t += 0.1
    assert r.sample_now() == ["s"]
    assert r.snapshot()["_meta"]["sources"]["s"]["error"] == "boom"


def test_fast_sampling_keeps_the_history_cadence_of_interval():
    s = Fake("s", ["n"], hist=["n"], interval=1.0, fast_interval=0.25)
    s.queue = [{"n": i} for i in range(100)]
    r, c, hits = rig(s)
    r.watch({"n"})
    for _ in range(40):                # 10 s of 4 Hz sampling
        r.poll_due()
        c.t += 0.25
    assert s.polls >= 38
    assert 9 <= len(r.snapshot()["n.history"]) <= 11   # ~1 sample per second, not 4


def test_normal_sampling_appends_history_on_every_poll():
    s = Fake("s", ["n"], hist=["n"], interval=1.0, fast_interval=0.25)
    s.queue = [{"n": i} for i in range(20)]
    r, c, hits = rig(s)
    for _ in range(5):                 # nothing watched; polled without the clock advancing
        r.poll_due(force=True)
    assert r.snapshot()["n.history"] == [0, 1, 2, 3, 4]


# ---------------------------------------------------------------- set_now_playing

class Playing(Fake):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.appid = None

    def set_now_playing(self, appid):
        if appid == "bad":
            raise ValueError("bad appid")
        self.appid = appid

    def poll(self):
        self.polls += 1
        return {"p": self.appid}


def test_instant_source_is_polled_before_set_now_playing_returns():
    s = Playing("steam", ["p"], instant=True)
    r, c, hits = rig(s)
    r.watch({"p"})
    r.set_now_playing(570)
    assert r.snapshot()["p"] == 570 and s.polls == 1 and len(hits) == 1


def test_slow_source_is_only_woken_not_polled_inline():
    s = Playing("web", ["p"])
    r, c, hits = rig(s)
    r.poll_due()
    r.set_now_playing(570)
    assert s.polls == 1 and r._slots[0].next_due == 0.0 and r._slots[0].wake.is_set()


def test_bad_appid_polls_nothing():
    s = Playing("steam", ["p"], instant=True)
    r, c, hits = rig(s)
    with pytest.raises(ValueError):
        r.set_now_playing("bad")
    assert s.polls == 0 and hits == []


def test_a_source_and_set_now_playing_never_poll_at_the_same_time():
    inside, worst = [0], [0]

    class Slow(Playing):
        def poll(self):
            inside[0] += 1
            worst[0] = max(worst[0], inside[0])
            time.sleep(0.05)
            inside[0] -= 1
            return {"p": 1}

    s = Slow("steam", ["p"], instant=True)
    r, c, hits = rig(s)
    ts = [threading.Thread(target=r.set_now_playing, args=(570,)) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert worst[0] == 1


# ---------------------------------------------------------------- threads: the real wake path

def test_poll_thread_delivers_the_event_quickly_and_watch_wakes_it():
    s = Fake("s", ["a"], interval=60.0, fast_interval=0.05)
    n = [0]

    def poll():
        n[0] += 1
        return {"a": n[0]}

    s.poll = poll
    r = Registry([s])
    got = threading.Event()
    r.subscribe(got.set)
    r.start()
    try:
        time.sleep(0.2)                   # first poll done, next one 60 s away
        r.watch({"a"})                    # must wake the thread and switch to 0.05 s
        got.clear()
        assert got.wait(1.0)
        n0 = n[0]
        time.sleep(0.5)
        assert n[0] - n0 >= 5
    finally:
        r.stop()


# ---------------------------------------------------------------- the real sources

def test_time_now_has_whole_second_resolution_and_samples_fast():
    t = datetime.datetime(2026, 11, 10, 8, 15, 59, 987654, tzinfo=datetime.timezone.utc)
    assert TimeSource("UTC", clock=lambda: t).poll()["time.now"].microsecond == 0
    assert TimeSource.fast_interval <= 0.25 < TimeSource.interval


def test_disk_temp_is_read_at_most_every_ttl(tmp_path):
    F.proc(tmp_path)
    F.hwmon(tmp_path, 0, "nvme", {"temp1_input": 40000, "temp1_label": "Composite"})
    c = Clock()
    s = SystemSource(str(tmp_path), clock=c)
    assert s.poll()["system.disk.temp"] == 40.0
    F.w(tmp_path, "sys/class/hwmon/hwmon0/temp1_input", "55000")
    c.t += DISK_TTL_S - 1
    assert s.poll()["system.disk.temp"] == 40.0     # cached
    c.t += 1.1
    assert s.poll()["system.disk.temp"] == 55.0     # refreshed


def test_system_source_samples_fast_but_keeps_polling_cheap_enough():
    assert 0 < SystemSource.fast_interval < SystemSource.interval


def test_hwmon_list_is_reused_but_sensor_values_are_always_fresh(tmp_path):
    F.proc(tmp_path)
    F.hwmon(tmp_path, 0, "k10temp", {"temp1_input": 50000, "temp1_label": "Tctl"})
    c = Clock()
    s = SystemSource(str(tmp_path), clock=c)
    assert s.poll()["system.cpu.temp"] == 50.0
    F.w(tmp_path, "sys/class/hwmon/hwmon0/temp1_input", "61000")
    c.t += 0.15
    assert s.poll()["system.cpu.temp"] == 61.0            # value re-read on every poll
    F.hwmon(tmp_path, 1, "nvme", {"temp1_input": 33000, "temp1_label": "Composite"})
    c.t += 0.15
    assert s.poll()["system.disk.temp"] is None            # new device not scanned yet (and disk temp is cached anyway)
    c.t += HWMON_TTL_S
    s._disk = None
    assert s.poll()["system.disk.temp"] == 33.0            # picked up after the TTL


def test_a_vanished_hwmon_value_is_none_not_an_error(tmp_path):
    F.proc(tmp_path)
    F.hwmon(tmp_path, 0, "k10temp", {"temp1_input": 50000, "temp1_label": "Tctl"})
    c = Clock()
    s = SystemSource(str(tmp_path), clock=c)
    assert s.poll()["system.cpu.temp"] == 50.0
    (tmp_path / "sys/class/hwmon/hwmon0/temp1_input").unlink()
    c.t += 0.15
    assert s.poll()["system.cpu.temp"] is None
