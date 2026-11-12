"""IANA time zones, with or without the stdlib `zoneinfo` module.

Decky Loader's bundled Python has no `zoneinfo`. `get_zone` uses it when importable and otherwise reads the system's
compiled zone files (`/usr/share/zoneinfo/<name>`, TZif v2+ plus its POSIX-TZ footer) with `TzFile`, so a named zone
gives the same answer on both paths. Unknown or unreadable names give None on both. No third-party code, stdlib in
Decky's bundle only.
"""
from __future__ import annotations

import bisect
import calendar
import os
import datetime as dt
import re
import struct

TZPATH = ("/usr/share/zoneinfo", "/usr/lib/zoneinfo", "/usr/share/lib/zoneinfo", "/etc/zoneinfo")
_EPOCH_ORD = 719163   # date(1970, 1, 1).toordinal()
_cache: dict[str, dt.tzinfo | None] = {}


def get_zone(name: str) -> dt.tzinfo | None:
    """tzinfo for an IANA name ('Europe/Berlin', 'UTC'), None if unknown. Cached: zones are immutable."""
    if name not in _cache:
        _cache[name] = _load(name)
    return _cache[name]


def _load(name: str) -> dt.tzinfo | None:
    try:
        import zoneinfo  # optional: absent in Decky's bundled Python
    except ImportError:
        return TzFile.from_name(name)
    try:
        return zoneinfo.ZoneInfo(name)
    except (KeyError, ValueError, OSError):   # ZoneInfoNotFoundError is a KeyError
        return None


# -- POSIX TZ footer ("CET-1CEST,M3.5.0,M10.5.0/3") ---------------------------------------------------------------
_NAME = r"(<[^>]+>|[A-Za-z]{3,})"
_OFF = r"([+-]?\d{1,3}(?::\d{1,2}){0,2})"
_POSIX = re.compile(rf"{_NAME}{_OFF}(?:{_NAME}{_OFF}?(?:,([^,]+),([^,]+))?)?$")
_RULE = re.compile(r"(?:M(\d{1,2})\.([1-5])\.([0-6])|J(\d{1,3})|(\d{1,3}))(?:/([+-]?\d{1,3}(?::\d{1,2}){0,2}))?$")


def _hms(s: str) -> int:
    sign = -1 if s.startswith("-") else 1
    h, m, sec = (int(p) for p in (s.lstrip("+-").split(":") + ["0", "0"])[:3])
    return sign * (h * 3600 + m * 60 + sec)


def _rule_wall(rule: str, year: int) -> int:
    """Seconds since 1970 (wall clock as if UTC) of a POSIX DST rule in `year`."""
    m = _RULE.match(rule)
    if not m:
        raise ValueError(f"bad POSIX rule {rule!r}")
    mon, week, wday, julian, zero, at = m.groups()
    if mon:
        mon, week, wday = int(mon), int(week), int(wday)
        first = (dt.date(year, mon, 1).weekday() + 1) % 7   # 0 = Sunday, as in POSIX
        day = 1 + (wday - first) % 7 + 7 * (week - 1)
        if day > calendar.monthrange(year, mon)[1]:
            day -= 7                                         # week 5 means "the last"
        ordinal = dt.date(year, mon, day).toordinal()
    elif julian:   # Jn: 1..365, Feb 29 never counted
        n = int(julian)
        ordinal = dt.date(year, 1, 1).toordinal() + n - 1 + (1 if calendar.isleap(year) and n >= 60 else 0)
    else:
        ordinal = dt.date(year, 1, 1).toordinal() + int(zero)
    return (ordinal - _EPOCH_ORD) * 86400 + (_hms(at) if at else 7200)


class _Posix:
    def __init__(self, text: str):
        m = _POSIX.match(text)
        if not m:
            raise ValueError(f"bad POSIX TZ {text!r}")
        sname, soff, dname, doff, start, end = m.groups()
        o = -_hms(soff)                                         # POSIX offsets count west of UTC
        self.std = (o, False, sname.strip("<>"), o)
        self.dst = None
        if dname:
            self.dst = (-_hms(doff) if doff else o + 3600, True, dname.strip("<>"), o)
            self.start, self.end = start or "M3.2.0", end or "M11.1.0"   # POSIX default when the rule is omitted
            _rule_wall(self.start, 2000), _rule_wall(self.end, 2000)       # reject a malformed file now, not on first use

    def at(self, t: int) -> tuple[int, bool, str, int]:
        if self.dst is None:
            return self.std
        year = (dt.datetime(1970, 1, 1) + dt.timedelta(seconds=t + self.std[0])).year
        on = _rule_wall(self.start, year) - self.std[0]   # starts in standard time
        off = _rule_wall(self.end, year) - self.dst[0]    # ends in daylight time
        in_dst = on <= t < off if on < off else not (off <= t < on)   # southern hemisphere wraps the new year
        return self.dst if in_dst else self.std


# -- TZif ---------------------------------------------------------------------------------------------------------
class TzFile(dt.tzinfo):
    """A zone read from a compiled TZif file (RFC 8536, version 2 or newer). Immutable once built."""

    def __init__(self, key: str, data: bytes):
        if data[:4] != b"TZif" or data[4:5] < b"2":
            raise ValueError("not a TZif v2+ file")

        def header(o):
            return struct.unpack(">6l", data[o + 20:o + 44])   # isut, isstd, leap, time, type, char counts

        isut, isstd, leap, nt, ny, nc = header(0)
        o = 44 + nt * 5 + ny * 6 + nc + leap * 8 + isstd + isut   # skip the 32-bit v1 block
        isut, isstd, leap, nt, ny, nc = header(o)
        o += 44
        self.key = key
        self._times = struct.unpack(f">{nt}q", data[o:o + nt * 8])
        idx = data[o + nt * 8:o + nt * 9]
        o += nt * 9
        raw = [struct.unpack(">lBB", data[o + 6 * i:o + 6 * i + 6]) for i in range(ny)]
        chars = data[o + 6 * ny:o + 6 * ny + nc]
        o += 6 * ny + nc + leap * 12 + isstd + isut
        types = [(off, bool(d), chars[a:chars.index(b"\0", a)].decode()) for off, d, a in raw]
        std = next((t[0] for t in types if not t[1]), types[0][0])   # standard offset in force; dst() is relative to it
        self._first = (*types[0], std)
        self._by_time = []
        for i in idx:
            std = types[i][0] if not types[i][1] else std
            self._by_time.append((*types[i], std))
        footer = data[o:].split(b"\n")
        self._posix = _Posix(footer[1].decode()) if len(footer) > 2 and footer[1] else None
        if not types or (not nt and self._posix is None):
            raise ValueError("zone has no rules")

    @classmethod
    def from_name(cls, name: str) -> TzFile | None:
        if not name or name.startswith(("/", ".")) or ".." in name or "\\" in name or "\0" in name:
            return None
        for base in TZPATH:
            try:
                with open(f"{base}/{name}", "rb") as f:
                    return cls(name, f.read())
            except (OSError, ValueError, struct.error, IndexError):   # missing, a directory, not TZif, truncated
                continue
        return None

    def _at(self, t: int) -> tuple[int, bool, str, int]:
        """(UTC offset seconds, is_dst, abbreviation, standard offset) at UTC instant `t` (seconds since 1970)."""
        if self._posix and (not self._times or t >= self._times[-1]):
            return self._posix.at(t)
        i = bisect.bisect_right(self._times, t) - 1
        return self._by_time[i] if i >= 0 else self._first

    @staticmethod
    def _secs(d: dt.datetime) -> int:   # a naive/aware datetime's fields as seconds since 1970
        return (d.toordinal() - _EPOCH_ORD) * 86400 + d.hour * 3600 + d.minute * 60 + d.second

    def _wall(self, d: dt.datetime) -> tuple[int, bool, str, int]:
        """The rule for a wall-clock time. Ambiguous: `fold` picks the occurrence. Skipped (gap): the pre-gap rule
        for fold=0, the post-gap rule for fold=1, as zoneinfo does. Assumes one transition per day at most."""
        w = self._secs(d)
        before, after = self._at(w - 86400), self._at(w + 86400)
        offs = [before[0]] + [after[0]] * (after[0] != before[0])   # pre- then post-transition offset
        ok = [r for o in offs if (r := self._at(w - o))[0] == o]   # those that map back to this wall time
        if len(ok) == 2:
            return ok[d.fold]
        return ok[0] if ok else (after if d.fold else before)

    def utcoffset(self, d):
        return None if d is None else dt.timedelta(seconds=self._wall(d)[0])

    def tzname(self, d):
        return None if d is None else self._wall(d)[2]

    def dst(self, d):
        if d is None:
            return None
        off, is_dst, _, std = self._wall(d)
        return dt.timedelta(seconds=off - std) if is_dst else dt.timedelta(0)

    def fromutc(self, d):
        off = self._at(self._secs(d))[0]
        wall = d + dt.timedelta(seconds=off)
        return wall.replace(fold=int(self._wall(wall.replace(fold=0))[0] != off))

    def __reduce__(self):   # copy.deepcopy (the data registry snapshots datetimes) and pickle rebuild it by name
        return get_zone, (self.key,)

    def __repr__(self):
        return f"TzFile({self.key!r})"


def posix_tz(name: str | None = None, localtime: str = "/etc/localtime") -> str:
    """POSIX TZ rule ('CET-1CEST,M3.5.0,M10.5.0/3') for IANA `name`, else for the system zone; 'UTC0' if unreadable.

    This is the TZif footer, so the panel (which has no zone database) follows the same DST rules as the host.
    Never longer than 63 bytes (the panel's tz field)."""
    paths = []
    if name:
        if not name.startswith(("/", ".")) and ".." not in name and "\\" not in name and "\0" not in name:
            paths = [f"{b}/{name}" for b in TZPATH]
    else:
        env = os.environ.get("TZ", "").lstrip(":")
        if env and not env.startswith(("/", ".")) and ".." not in env:
            paths = [f"{b}/{env}" for b in TZPATH]
        paths.append(localtime)
    for path in paths:
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            continue
        if data[:4] != b"TZif" or data[4:5] < b"2" or not data.endswith(b"\n"):
            continue
        text = data.rstrip(b"\n").rsplit(b"\n", 1)[-1]
        try:
            rule = text.decode("ascii")
            _Posix(rule)
        except (UnicodeDecodeError, ValueError):
            continue
        if rule and len(rule) <= 63 and rule.isprintable():
            return rule
    return "UTC0"
