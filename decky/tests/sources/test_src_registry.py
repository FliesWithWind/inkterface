import datetime
import json
import threading
import time

import pytest
import sourcefs as F

from inkterface_core import sources as S
from inkterface_core.sources import Registry, Source, SourceError, TimeSource
from inkterface_core.sources import timesrc


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


class Fake(Source):
    def __init__(self, name, paths, hist=(), interval=10.0, **kw):
        super().__init__()
        self.name, self.paths, self.history_paths, self.interval = name, tuple(paths), tuple(hist), interval
        for k, v in kw.items():
            setattr(self, k, v)
        self.queue = []
        self.polls = 0

    def poll(self):
        self.polls += 1
        r = self.queue.pop(0) if self.queue else {}
        if isinstance(r, Exception):
            raise r
        return r


def reg(*sources, **kw):
    c = Clock()
    return Registry(sources, clock=c, **kw), c


def test_failure_isolation_one_bad_source_does_not_break_others():
    good, bad = Fake("good", ["a"]), Fake("bad", ["b"])
    good.queue = [{"a": 1}]
    bad.queue = [RuntimeError("boom")]
    r, c = reg(bad, good)
    assert r.poll_due() == ["bad", "good"]
    snap = r.snapshot()
    assert snap["a"] == 1 and snap["b"] is None
    assert snap["_meta"]["sources"]["bad"]["error"] == "boom"
    assert snap["_meta"]["sources"]["good"]["error"] is None


def test_exception_without_message_still_has_error_text_and_non_dict_is_error():
    s = Fake("s", ["a"])
    s.queue = [KeyError()]
    r, c = reg(s)
    r.poll_due()
    assert s.last_error == "KeyError"
    c.t += 1000
    s.queue = [[1, 2]]
    r.poll_due()
    assert "expected dict" in s.last_error


def test_error_clears_after_success_and_old_values_survive_failure():
    s = Fake("s", ["a"])
    s.queue = [{"a": 1}, SourceError("net down"), {"a": 2}]
    r, c = reg(s)
    r.poll_due()
    c.t += 100
    r.poll_due()
    snap = r.snapshot()
    assert snap["a"] == 1 and s.last_error == "net down"  # last good value kept
    c.t += 100
    r.poll_due()
    assert r.snapshot()["a"] == 2 and s.last_error is None


def test_scheduling_respects_interval():
    s = Fake("s", ["a"], interval=10)
    r, c = reg(s)
    assert r.poll_due() == ["s"]
    assert r.poll_due() == []
    c.t += 9.9
    assert r.poll_due() == []
    c.t += 0.2
    assert r.poll_due() == ["s"]
    assert r.poll_due(force=True) == ["s"] and s.polls == 3


def test_exponential_backoff_capped_and_reset():
    s = Fake("s", ["a"], interval=10, max_backoff=50)
    s.queue = [RuntimeError("x")] * 6 + [{"a": 1}]
    r, c = reg(s)
    gaps = []
    for _ in range(6):
        r.poll_due()
        slot = r._slots[0]
        gaps.append(slot.next_due - c.t)
        c.t = slot.next_due
    assert gaps == [20, 40, 50, 50, 50, 50]
    r.poll_due()
    assert r._slots[0].next_due - c.t == 10 and r._slots[0].fails == 0


def test_history_ring_buffer_length_and_order():
    s = Fake("s", ["n"], hist=["n"], interval=1)
    r, c = reg(s)
    s.queue = [{"n": i} for i in range(75)]
    for _ in range(75):
        r.poll_due(force=True)
    h = r.snapshot()["n.history"]
    assert len(h) == 60 and h[0] == 15 and h[-1] == 74  # oldest first


def test_history_custom_length_skips_none_bool_nan_and_strings():
    s = Fake("s", ["n"], hist=["n"])
    s.queue = [{"n": 1}, {"n": None}, {"n": True}, {"n": float("nan")}, {"n": float("inf")}, {"n": "7"}, {"n": 2.5}, {}]
    r, c = reg(s, history=3)
    for _ in range(8):
        r.poll_due(force=True)
    assert r.snapshot()["n.history"] == [1, 2.5]


def test_history_present_and_empty_before_first_poll():
    r, c = reg(Fake("s", ["n"], hist=["n"]))
    snap = r.snapshot()
    assert snap["n"] is None and snap["n.history"] == []
    assert snap["_meta"]["sources"]["s"]["stale"] and snap["_meta"]["sources"]["s"]["age"] is None


def test_history_survives_a_failed_poll_and_expiry():
    s = Fake("s", ["n"], hist=["n"], interval=1)
    s.queue = [{"n": 1}, {"n": 2}]
    r, c = reg(s)
    r.poll_due(force=True)
    r.poll_due(force=True)
    c.t += 1000
    snap = r.snapshot()
    assert snap["n"] is None and snap["n.history"] == [1, 2]


def test_staleness_then_expiry():
    s = Fake("s", ["a"], interval=10)  # stale after 30 s, expires after 100 s
    s.queue = [{"a": 5}]
    r, c = reg(s)
    r.poll_due()
    snap = r.snapshot()
    assert snap["a"] == 5 and snap["_meta"]["stale"] == []
    c.t += 30.5
    snap = r.snapshot()
    assert snap["a"] == 5 and snap["_meta"]["stale"] == ["a"] and snap["_meta"]["sources"]["s"]["stale"] is True
    c.t += 100
    snap = r.snapshot()
    assert snap["a"] is None and snap["_meta"]["stale"] == ["a"]


def test_custom_stale_and_expire_thresholds():
    s = Fake("s", ["a"], interval=10, stale_after=1, expire_after=2)
    s.queue = [{"a": 1}]
    r, c = reg(s)
    r.poll_due()
    c.t += 1.5
    assert r.snapshot()["_meta"]["stale"] == ["a"] and r.snapshot()["a"] == 1
    c.t += 1
    assert r.snapshot()["a"] is None


def test_unrecovered_source_goes_stale_even_if_never_succeeded():
    s = Fake("s", ["a"])
    s.queue = [RuntimeError("x")]
    r, c = reg(s)
    r.poll_due()
    assert r.snapshot()["_meta"]["stale"] == ["a"]


def test_snapshot_is_a_copy():
    s = Fake("s", ["a", "n"], hist=["n"])
    s.queue = [{"a": {"x": [1]}, "n": 1}]
    r, c = reg(s)
    r.poll_due()
    snap = r.snapshot()
    snap["a"]["x"].append(2)
    snap["n.history"].append(99)
    again = r.snapshot()
    assert again["a"] == {"x": [1]} and again["n.history"] == [1]


def test_merge_dicts_none_never_overrides_later_wins():
    local, web = Fake("local", ["u", "r", "k"]), Fake("web", ["u", "r", "k"])
    local.queue = [{"u": {"persona": "L", "level": None, "hours": 5.0}, "r": [1], "k": None}]
    web.queue = [{"u": {"persona": "W", "level": 9, "hours": None}, "r": None, "k": 4}]
    r, c = reg(local, web)
    r.poll_due()
    snap = r.snapshot()
    assert snap["u"] == {"persona": "W", "level": 9, "hours": 5.0}
    assert snap["r"] == [1] and snap["k"] == 4


def test_merge_empty_list_from_later_source_is_authoritative():
    local, web = Fake("local", ["r"]), Fake("web", ["r"])
    local.queue = [{"r": [1, 2]}]
    web.queue = [{"r": []}]
    r, c = reg(local, web)
    r.poll_due()
    assert r.snapshot()["r"] == []


def test_expired_second_source_lets_first_show_through():
    local, web = Fake("local", ["u"], interval=10), Fake("web", ["u"], interval=10)
    local.queue = [{"u": {"persona": "L", "level": None}}, {"u": {"persona": "L", "level": None}}]
    web.queue = [{"u": {"persona": "W", "level": 9}}, RuntimeError("down")]
    r, c = reg(local, web)
    r.poll_due()
    assert r.snapshot()["u"]["level"] == 9
    for _ in range(12):
        c.t += 10
        r.poll_due()
        local.queue.append({"u": {"persona": "L", "level": None}})
    assert r.snapshot()["u"] == {"persona": "L", "level": None}


def test_duplicate_source_name_rejected():
    r, c = reg(Fake("s", []))
    with pytest.raises(ValueError):
        r.add(Fake("s", []))


def test_set_now_playing_forwarded_and_scheduled():
    class NP(Fake):
        def set_now_playing(self, a):
            self.got = a

    np_, other = NP("np", ["a"]), Fake("other", ["b"])
    r, c = reg(np_, other)
    r.poll_due()
    assert r.poll_due() == []
    r.set_now_playing(730)
    assert np_.got == 730
    assert r.poll_due() == ["np"]  # only the one that tracks it is made due


def test_set_now_playing_invalid_appid_propagates():
    r = S.default_registry({"steam_dir": "/nonexistent"}, network=False)
    with pytest.raises(ValueError):
        r.set_now_playing("nope")


def test_dumps_serialises_datetimes():
    r, c = reg(TimeSource(clock=lambda: datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.timezone.utc)))
    r.poll_due()
    out = json.loads(S.dumps(r.snapshot()))
    assert out["time.now"] == "2026-01-02T03:04:05+00:00"
    with pytest.raises(TypeError):
        S.jsonable(object())


# -- threads -------------------------------------------------------------------------------------

def wait_for(cond, t=3.0):
    end = time.monotonic() + t
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.005)
    return False


def test_threads_poll_independently_and_stop():
    fast, slow = Fake("fast", ["a"], hist=["a"], interval=0.01), Fake("slow", ["b"], interval=60)
    gate = threading.Event()
    orig = slow.poll

    def blocking():
        gate.wait(2)
        return orig()

    slow.poll = blocking
    fast.queue = [{"a": i} for i in range(1000)]
    r = Registry([slow, fast])
    r.start()
    r.start()  # idempotent
    try:
        assert wait_for(lambda: fast.polls >= 5)  # a blocked slow source does not hold the fast one
        assert slow.polls == 0 and r.snapshot()["_meta"]["sources"]["slow"]["age"] is None  # still blocked inside poll()
    finally:
        gate.set()
        r.stop()
    n = fast.polls
    time.sleep(0.05)
    assert fast.polls == n  # stopped
    assert all(not t.is_alive() for t in r._threads) and r._threads == []


def test_thread_wakes_for_set_now_playing():
    class NP(Fake):
        def set_now_playing(self, a):
            self.got = a

    s = NP("np", ["a"], interval=60)
    r = Registry([s])
    r.start()
    try:
        assert wait_for(lambda: s.polls == 1)
        r.set_now_playing(5)
        assert wait_for(lambda: s.polls == 2)
    finally:
        r.stop()


def test_thread_survives_source_exception():
    s = Fake("s", ["a"], interval=0.01, max_backoff=0.02)
    s.queue = [RuntimeError("x"), RuntimeError("y"), {"a": 1}]
    r = Registry([s])
    r.start()
    try:
        assert wait_for(lambda: r.snapshot()["a"] == 1)
    finally:
        r.stop()


# -- TimeSource ----------------------------------------------------------------------------------

def test_time_source_fixed_clock_and_zone():
    now = datetime.datetime(2026, 7, 1, 12, 0, tzinfo=datetime.timezone.utc)
    s = TimeSource("Asia/Tokyo", clock=lambda: now)
    r = s.poll()
    if s._zone is None:  # tzdata missing on this box
        pytest.skip("no tz database")
    assert r["time.tz"] == "Asia/Tokyo" and r["time.now"].hour == 21 and r["time.now"] == now
    assert r["time.now"].tzinfo is not None and s.warning is None


def test_time_source_bad_zone_falls_back_with_warning():
    for bad in ("Not/AZone", "../etc/passwd"):
        s = TimeSource(bad)
        assert s.warning and "unavailable" in s.warning
        r = s.poll()
        assert r["time.now"].tzinfo is not None and r["time.tz"]


def test_time_source_default_is_aware_local():
    r = TimeSource().poll()
    assert r["time.now"].tzinfo is not None and isinstance(r["time.tz"], str) and r["time.tz"]


def test_local_tzname_variants(tmp_path, monkeypatch):
    monkeypatch.setenv("TZ", ":Europe/Paris")
    assert timesrc._local_tzname() == "Europe/Paris"
    monkeypatch.delenv("TZ")
    zi = tmp_path / "usr/share/zoneinfo/Europe"
    zi.mkdir(parents=True)
    (zi / "Berlin").write_text("x")
    (tmp_path / "localtime").symlink_to(zi / "Berlin")
    assert timesrc._local_tzname(str(tmp_path / "localtime")) == "Europe/Berlin"
    assert timesrc._local_tzname(str(tmp_path / "missing")) != ""  # falls back to the abbreviation


# -- default_registry ---------------------------------------------------------------------------

def test_default_registry_wiring_offline_and_online(tmp_path):
    F.amd_apu(tmp_path)
    off = S.default_registry({}, root=str(tmp_path), home=str(tmp_path / "nohome"), network=False)
    assert [s.source.name for s in off._slots] == ["time", "system", "steam_local"]
    net = []
    on = S.default_registry({"weather_lat": 1, "weather_lon": 2, "units": "imperial", "steam_api_key": "k", "steamid64": "76561198000000001"},
                            fetch=lambda u, t: net.append(u) or b"{}", root=str(tmp_path), home=str(tmp_path / "nohome"))
    assert [s.source.name for s in on._slots] == ["time", "system", "steam_local", "steam_web", "weather"]
    on.poll_due(force=True)
    assert len(net) >= 2  # network sources use the injected fetch, nothing else
    snap = on.snapshot()
    assert snap["system.gpu.load"] == 37 and "weather" in snap and "steam.friends_online" in snap
    assert snap["_meta"]["sources"]["weather"]["error"]  # {} is not a weather payload: isolated, reported


def test_every_source_poll_returns_only_declared_paths(tmp_path):
    F.amd_apu(tmp_path)
    for name, src, args in (
        ("system", S.SystemSource(str(tmp_path)), ()),
        ("time", TimeSource(), ()),
        ("steam_local", S.SteamLocal(steam_dir=str(tmp_path)), ()),
        ("steam_web", S.SteamWeb(None, None), ()),
        ("weather", S.WeatherSource(None, None), ()),
    ):
        assert set(src.poll()) <= set(src.paths), name
        assert set(src.history_paths) <= set(src.paths), name


def test_binding_paths_present_even_with_nothing_available(tmp_path):
    """The screen engine binds to these exact names; none may be missing from a cold snapshot."""
    r = S.default_registry({}, root=str(tmp_path), home=str(tmp_path), network=True)
    snap = r.snapshot()
    required = ["time.now", "time.tz", "steam.now_playing", "steam.user", "steam.recent", "steam.friends_online", "weather"]
    numeric = ["system.cpu.load", "system.cpu.temp", "system.cpu.clock", "system.gpu.load", "system.gpu.temp", "system.gpu.clock",
               "system.gpu.mem_clock", "system.mem.used_pct", "system.fan.rpm", "system.power.package_w"]
    for p in required + numeric:
        assert p in snap and snap[p] is None, p
    for p in numeric:
        assert snap[p + ".history"] == []


def test_snapshot_key_shape_is_flat_dotted_with_history_siblings(tmp_path):
    """Contract with the screen engine: system.* metrics and their .history are sibling dotted keys."""
    F.amd_apu(tmp_path)
    r = S.default_registry({}, root=str(tmp_path), home=str(tmp_path), network=True)
    r.poll_due(force=True)
    snap = r.snapshot()
    metrics = ["system.cpu.load", "system.cpu.temp", "system.cpu.clock", "system.gpu.load", "system.gpu.temp", "system.gpu.clock",
               "system.gpu.mem_clock", "system.mem.used_pct", "system.fan.rpm", "system.power.package_w", "system.disk.temp"]
    expected = {"time.now", "time.tz", "steam.now_playing", "steam.user", "steam.recent", "steam.friends_online", "weather", "_meta"}
    expected |= set(metrics) | {m + ".history" for m in metrics}
    assert set(snap) == expected
    assert not any(k in snap for k in ("system", "system.cpu", "system.gpu", "time", "steam"))  # no nested system/time/steam dicts
    for m in metrics:
        assert isinstance(snap[m + ".history"], list)
        assert snap[m] is None or isinstance(snap[m], (int, float))
    assert snap["system.gpu.load"] == 37 and snap["system.gpu.load.history"] == [37]
