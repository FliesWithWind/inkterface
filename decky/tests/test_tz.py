"""inkterface_core.tz: named zones with and without the stdlib `zoneinfo` (Decky's bundled Python lacks it).

The no-zoneinfo path (`TzFile`) is checked against `zoneinfo` itself as the oracle, so both paths must agree."""
import copy
import os
import datetime as dt
import pickle
import sys
import zoneinfo

import pytest

from inkterface_core import tz
from inkterface_core.sources import TimeSource

UTC = dt.timezone.utc
ZONES = ["Europe/Berlin", "America/New_York", "Australia/Sydney", "Asia/Tokyo", "Asia/Kolkata", "UTC", "Europe/Dublin",
         "America/St_Johns", "Africa/Casablanca", "Antarctica/Troll", "Australia/Lord_Howe", "America/Sao_Paulo"]
pytestmark = pytest.mark.skipif(tz.TzFile.from_name("Europe/Berlin") is None, reason="no system tz database")


@pytest.fixture(autouse=True)
def fresh_cache():
    tz._cache.clear()
    yield
    tz._cache.clear()


@pytest.fixture
def no_zoneinfo(monkeypatch):
    monkeypatch.setitem(sys.modules, "zoneinfo", None)   # `import zoneinfo` raises ImportError, like in Decky


def ts(t):
    return dt.datetime.fromtimestamp(t, UTC)


def same(a, b):
    """Same wall time, fold, offset and abbreviation; dst() only by truthiness (zoneinfo measures 1945 double summer time
    against the previous rule, we against standard time: no caller needs more than "is DST in force")."""
    assert (a.replace(tzinfo=None), a.fold, a.utcoffset(), a.tzname(), bool(a.dst())) == \
           (b.replace(tzinfo=None), b.fold, b.utcoffset(), b.tzname(), bool(b.dst())), (a, b)


# ---- both paths agree ------------------------------------------------------------------------------------

@pytest.mark.parametrize("name", ZONES)
def test_tzfile_matches_zoneinfo_at_every_sampled_instant_and_around_each_transition(name):
    mine, ref = tz.TzFile.from_name(name), zoneinfo.ZoneInfo(name)
    prev, step = None, 100_003   # ~1.16 days: finer than any gap between two transitions
    for t in range(-2_000_000_000, 2_600_000_000, step):   # 1906 .. 2052: file transitions, then the POSIX footer
        same(ts(t).astimezone(mine), ts(t).astimezone(ref))
        cur = ts(t).astimezone(ref).utcoffset()
        if prev is not None and cur != prev:   # bisect to the second of the change, test both sides of it
            lo, hi = t - step, t
            while hi - lo > 1:
                mid = (lo + hi) // 2
                lo, hi = (mid, hi) if ts(mid).astimezone(ref).utcoffset() == prev else (lo, mid)
            for x in (lo - 3600, lo, hi, hi + 3600):
                same(ts(x).astimezone(mine), ts(x).astimezone(ref))
        prev = cur


@pytest.mark.parametrize("name,wall", [
    ("Europe/Berlin", dt.datetime(2026, 10, 25, 2, 30)),     # ambiguous: clocks go back
    ("Europe/Berlin", dt.datetime(2026, 3, 29, 2, 30)),      # skipped: clocks go forward
    ("America/New_York", dt.datetime(2026, 11, 1, 1, 30)),
    ("Australia/Sydney", dt.datetime(2026, 4, 5, 2, 30)),    # southern hemisphere, ends in April
    ("Australia/Lord_Howe", dt.datetime(2026, 10, 4, 2, 15)),  # 30-minute DST
    ("Europe/Dublin", dt.datetime(2026, 10, 25, 1, 30)),     # "negative" DST in the tz database
])
def test_wall_times_in_gaps_and_overlaps_match_zoneinfo(name, wall):
    mine, ref = tz.TzFile.from_name(name), zoneinfo.ZoneInfo(name)
    for fold in (0, 1):
        w = wall.replace(fold=fold)
        a, b = w.replace(tzinfo=mine), w.replace(tzinfo=ref)
        assert (a.utcoffset(), a.tzname(), bool(a.dst())) == (b.utcoffset(), b.tzname(), bool(b.dst())), (name, fold)
        assert a.astimezone(UTC) == b.astimezone(UTC)


# ---- get_zone picks the path -----------------------------------------------------------------------------

def test_get_zone_uses_zoneinfo_when_present():
    assert isinstance(tz.get_zone("Europe/Berlin"), zoneinfo.ZoneInfo)


def test_get_zone_falls_back_to_the_system_files_without_zoneinfo(no_zoneinfo):
    z = tz.get_zone("Europe/Berlin")
    assert isinstance(z, tz.TzFile) and tz.get_zone("Europe/Berlin") is z   # cached
    winter, summer = dt.datetime(2026, 1, 15, 12, tzinfo=UTC), dt.datetime(2026, 7, 15, 12, tzinfo=UTC)
    assert (winter.astimezone(z).hour, summer.astimezone(z).hour) == (13, 14)
    assert summer.astimezone(z).tzname() == "CEST"


@pytest.mark.parametrize("bad", ["Mars/Base", "", "../etc/passwd", "/etc/passwd", "Europe/../UTC", "Europe", "zone.tab",
                                 "UTC\0", "Europe\\Berlin", ".hidden", "iso3166.tab"])
@pytest.mark.parametrize("have_zoneinfo", [True, False])
def test_unknown_or_hostile_names_give_none_on_both_paths(bad, have_zoneinfo, monkeypatch):
    if not have_zoneinfo:
        monkeypatch.setitem(sys.modules, "zoneinfo", None)
    assert tz.get_zone(bad) is None


def raw_zone(name):
    return next(open(f"{base}/{name}", "rb").read() for base in tz.TZPATH if os.path.isfile(f"{base}/{name}"))


def test_unreadable_files_give_none(tmp_path, monkeypatch):
    raw = raw_zone("Europe/Berlin")
    (tmp_path / "Junk").write_bytes(b"not a tz file at all")
    (tmp_path / "Cut").write_bytes(raw[:60])                  # truncated mid-header
    (tmp_path / "Cut2").write_bytes(raw[:len(raw) // 2])      # truncated mid-data
    (tmp_path / "V1").write_bytes(b"TZif" + b"\0" * 40)        # version 1: no 64-bit data, not supported
    (tmp_path / "Good").write_bytes(raw)
    monkeypatch.setattr(tz, "TZPATH", (str(tmp_path),))
    for name in ("Junk", "Cut", "Cut2", "V1", "Missing"):
        assert tz.TzFile.from_name(name) is None, name
    assert tz.TzFile.from_name("Good") is not None


def test_tzpath_is_searched_in_order(tmp_path, monkeypatch):
    (tmp_path / "Mine").write_bytes(raw_zone("Asia/Tokyo"))
    monkeypatch.setattr(tz, "TZPATH", ("/nonexistent", str(tmp_path)))
    assert tz.TzFile.from_name("Mine").utcoffset(dt.datetime(2026, 1, 1)) == dt.timedelta(hours=9)


def test_zone_survives_copy_and_pickle(no_zoneinfo):
    z = tz.get_zone("Europe/Berlin")
    d = dt.datetime(2026, 7, 1, 12, tzinfo=z)
    assert copy.deepcopy(d).utcoffset() == d.utcoffset() == dt.timedelta(hours=2)   # the data registry deep-copies snapshots
    assert pickle.loads(pickle.dumps(d)).utcoffset() == d.utcoffset()


# ---- POSIX footer rules (the part that covers every year after the file's last transition) ----------------

@pytest.mark.parametrize("rule,when,expect", [
    ("CET-1CEST,M3.5.0,M10.5.0/3", dt.datetime(2040, 7, 1, tzinfo=UTC), (7200, True, "CEST")),
    ("CET-1CEST,M3.5.0,M10.5.0/3", dt.datetime(2040, 1, 1, tzinfo=UTC), (3600, False, "CET")),
    ("CET-1CEST,M3.5.0,M10.5.0/3", dt.datetime(2040, 3, 25, 0, 59, tzinfo=UTC), (3600, False, "CET")),   # last Sunday 02:00 local
    ("CET-1CEST,M3.5.0,M10.5.0/3", dt.datetime(2040, 3, 25, 1, 0, tzinfo=UTC), (7200, True, "CEST")),
    ("AEST-10AEDT,M10.1.0,M4.1.0/3", dt.datetime(2040, 1, 1, tzinfo=UTC), (39600, True, "AEDT")),         # southern: wraps the year
    ("AEST-10AEDT,M10.1.0,M4.1.0/3", dt.datetime(2040, 7, 1, tzinfo=UTC), (36000, False, "AEST")),
    ("<+03>-3", dt.datetime(2040, 7, 1, tzinfo=UTC), (10800, False, "+03")),                                # quoted name, no DST
    ("EST5EDT", dt.datetime(2040, 7, 1, tzinfo=UTC), (-14400, True, "EDT")),                                # POSIX default rules
    ("PST8PDT,J60,J300", dt.datetime(2040, 7, 1, tzinfo=UTC), (-25200, True, "PDT")),                       # Julian days
    ("PST8PDT,59,299", dt.datetime(2040, 12, 1, tzinfo=UTC), (-28800, False, "PST")),                       # zero-based days
    ("IST-1GMT0,M10.5.0,M3.5.0/1", dt.datetime(2040, 7, 1, tzinfo=UTC), (3600, False, "IST")),             # Dublin: DST is "standard"
    ("NZST-12NZDT,M9.5.0,M4.1.0/3", dt.datetime(2040, 1, 1, tzinfo=UTC), (46800, True, "NZDT")),
    ("<-0330>3:30<-0230>,M3.2.0,M11.1.0", dt.datetime(2040, 7, 1, tzinfo=UTC), (-9000, True, "-0230")),    # 30-minute offsets
    ("XXX3EDT4,M3.2.0/-1,M11.1.0/25", dt.datetime(2040, 7, 1, tzinfo=UTC), (-14400, True, "EDT")),   # times beyond 24h / negative
])
def test_posix_rules(rule, when, expect):
    assert tz._Posix(rule).at(int(when.timestamp()))[:3] == expect


@pytest.mark.parametrize("bad", ["", "CET", "12", "CET-1CEST,Q3.5.0,M10.5.0", "CET-1CEST,M13.5.0,M10.5.0", "CET-1CEST,M3.5.0,M10.5.0x"])
def test_bad_posix_rules_raise(bad):
    with pytest.raises(ValueError):
        tz._Posix(bad)


# ---- the two consumers work on the fallback path ---------------------------------------------------------

def test_time_source_without_zoneinfo_still_honours_the_zone(no_zoneinfo):
    s = TimeSource("America/New_York", clock=lambda: dt.datetime(2026, 7, 1, 12, tzinfo=UTC))
    assert not s.warning
    r = s.poll()
    assert r["time.tz"] == "America/New_York" and r["time.now"].hour == 8 and r["time.now"].tzname() == "EDT"


def test_time_source_without_zoneinfo_and_without_zone_files_uses_the_system_zone_with_a_warning(no_zoneinfo, monkeypatch):
    monkeypatch.setattr(tz, "TZPATH", ())   # e.g. a container without tzdata
    s = TimeSource("Europe/Berlin")
    assert "unavailable" in s.warning
    assert s._zone is None and s.poll()["time.now"].tzinfo is not None


def test_time_source_unknown_zone_warns_on_both_paths(no_zoneinfo):
    s = TimeSource("Mars/Base")
    assert s.warning and "unavailable" in s.warning and s.poll()["time.now"].tzinfo is not None


def test_clock_widget_without_zoneinfo(no_zoneinfo):
    from inkterface_core.screens import DataSnapshot
    from inkterface_core.screens import widgets as WG
    snap = DataSnapshot({"time.now": dt.datetime(2026, 11, 10, 13, 32, tzinfo=UTC), "time.tz": "Asia/Tokyo"})
    o = {"format": "%H:%M %Z", "timezone": "", "bind": None}
    assert WG.Clock.text(o, snap) == "22:32 JST"
    assert WG.Clock.text({**o, "timezone": "America/New_York"}, snap) == "08:32 EST"
    assert WG.Clock.text({**o, "timezone": "Mars/Base"}, snap) is None
