import pytest

from inkterface_core.engine import Scheduler, Settings


def mk(rot=("a", "b", "c"), interval=10, avail=("a", "b", "c", "now-playing"), **kw):
    return Scheduler(Settings(rotation=list(rot), rotation_interval_s=interval, **kw), avail)


def test_rotation_follows_the_clock():
    s = mk()
    got = [s.current(t) for t in (0, 9.9, 10, 19.9, 20, 30, 40)]
    assert got == ["a", "a", "b", "b", "c", "a", "b"]


def test_rotation_skips_missed_intervals_in_one_call():
    s = mk()
    assert s.current(0) == "a"
    assert s.current(35) == "a"  # 3 intervals later: a,b,c,a
    assert s.current(39.9) == "a" and s.current(40) == "b"  # slot boundary stays on the original grid


def test_rotation_ignores_screens_that_are_not_available():
    s = mk(rot=("a", "zzz", "b"))
    assert [s.current(t) for t in (0, 10, 20)] == ["a", "b", "a"]


def test_empty_or_unusable_rotation_falls_back_to_any_screen():
    assert mk(rot=()).current(0) == "a"
    assert mk(rot=("nope",), avail=("q", "z")).current(0) == "q"


def test_nothing_available_is_none():
    s = mk(avail=())
    assert s.current(0) is None
    assert s.current(100) is None


def test_pin_wins_over_rotation_and_now_playing():
    s = mk(pinned="b")
    s.set_now_playing(570)
    assert [s.current(t) for t in (0, 10, 20, 500)] == ["b"] * 4


def test_pin_to_unavailable_screen_is_ignored():
    s = mk(pinned="ghost")
    assert s.current(0) == "a"


def test_now_playing_switches_and_returns_to_the_same_slot():
    s = mk()
    assert s.current(0) == "a"
    assert s.current(12) == "b"
    s.set_now_playing(570)
    assert s.current(13) == "now-playing"
    assert s.current(500) == "now-playing"
    s.set_now_playing(None)
    assert s.current(512) == "b"  # b had 7 s left when the game started at t=13; held time does not count
    assert s.current(518.9) == "b"
    assert s.current(519) == "c"


def test_now_playing_off_or_zero_or_screen_missing_does_nothing():
    s = mk(auto_now_playing=False)
    s.set_now_playing(570)
    assert s.current(0) == "a"
    s = mk(avail=("a", "b", "c"))  # no now-playing screen installed
    s.set_now_playing(570)
    assert s.current(0) == "a"
    s = mk()
    s.set_now_playing(0)
    assert s.now_playing is None and s.current(0) == "a"


def test_custom_now_playing_screen():
    s = mk(now_playing_screen="game", avail=("a", "game"))
    s.set_now_playing(1)
    assert s.current(0) == "game"


def test_unpin_resumes_rotation_where_it_was():
    s = mk()
    s.current(0)
    assert s.current(11) == "b"
    s.settings.pinned = "c"
    assert s.current(12) == "c"  # b had 8 s left (slot 10..20)
    assert s.current(100) == "c"
    s.settings.pinned = None
    assert s.current(105) == "b"
    assert s.current(112.9) == "b"
    assert s.current(113) == "c"


def test_update_keeps_current_screen_when_still_listed():
    s = mk()
    s.current(0)
    assert s.current(11) == "b"
    s.set_available(["x", "b", "c"])
    s.update(Settings(rotation=["x", "b", "c"], rotation_interval_s=20), 12)
    assert s.current(12) == "b"
    assert s.current(31.9) == "b" and s.current(32) == "c"  # new interval counts from the update


def test_update_restarts_when_current_screen_dropped():
    s = mk()
    s.current(0)
    s.current(11)
    s.update(Settings(rotation=["c", "a"], rotation_interval_s=10), 12)
    assert s.current(12) == "c"


def test_update_while_held_resumes_cleanly():
    s = mk()
    s.current(0)
    s.set_now_playing(1)
    assert s.current(5) == "now-playing"
    s.update(Settings(rotation=["a", "b"], rotation_interval_s=10), 6)
    assert s.current(7) == "now-playing"
    s.set_now_playing(None)
    assert s.current(100) == "a"
    assert s.current(109.9) == "a" and s.current(110) == "b"


def test_set_available_changes_choice():
    s = mk(rot=("a", "b"))
    s.set_available(["b"])
    assert s.current(0) == "b"
