"""Data binding: the source registry, DataSnapshot and `{path:spec}` templates."""
from __future__ import annotations

import math
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

PLACEHOLDER = "--"

# kind -> meaning
#   number  int/float          series  list of numbers (oldest first)
#   string  str                path    file path of an image on the host
#   time    epoch secs or ISO 8601 str
NUMBER, STRING, SERIES, PATH, TIME, BOOL = "number", "string", "series", "path", "time", "bool"

# Binding-path contract shared with the data-source collectors. Numeric `system.*` metrics also
# publish `<path>.history` (last 60 samples, oldest first). Value and history are sibling flat keys
# ("system.cpu.load" and "system.cpu.load.history"), so a snapshot must use flat dotted keys there.
# `[n]` stands for any list index.
SOURCES: dict[str, str] = {
    "time.now": TIME,
    "time.tz": STRING,
    "weather.temp": NUMBER, "weather.condition": STRING, "weather.icon": STRING,
    "weather.high": NUMBER, "weather.low": NUMBER, "weather.units": STRING,
    "weather.sunrise": TIME, "weather.sunset": TIME,
    "steam.now_playing.appid": NUMBER,
    "steam.now_playing.name": STRING,
    "steam.now_playing.box_art_path": PATH,
    "steam.now_playing.session_minutes": NUMBER,
    "steam.now_playing.total_minutes": NUMBER,
    "steam.now_playing.last_achievement": STRING,
    "steam.user.persona": STRING, "steam.user.level": NUMBER,
    "steam.user.games_owned": NUMBER, "steam.user.hours_total": NUMBER,
    "steam.friends_online": NUMBER,
    "steam.recent[n].appid": NUMBER, "steam.recent[n].name": STRING,
    "steam.recent[n].minutes_2w": NUMBER, "steam.recent[n].minutes_total": NUMBER,
    "weather.forecast[n].date": TIME, "weather.forecast[n].high": NUMBER,
    "weather.forecast[n].low": NUMBER, "weather.forecast[n].icon": STRING,
}
for _p in ("cpu.load", "cpu.temp", "cpu.clock", "gpu.load", "gpu.temp", "gpu.clock", "gpu.mem_clock",
           "mem.used_pct", "fan.rpm", "power.package_w"):
    SOURCES[f"system.{_p}"] = NUMBER
    SOURCES[f"system.{_p}.history"] = SERIES

_INDEX = re.compile(r"\[\d+\]")

SETTING_KINDS = {"string": STRING, "enum": STRING, "number": NUMBER, "bool": BOOL}


def source_kind(path: str, settings_kinds: Mapping[str, str] | None = None) -> str | None:
    """Kind of a bindable path, or None when unknown. `settings.<key>` comes from the screen's schema."""
    if path.startswith("settings."):
        return (settings_kinds or {}).get(path[len("settings."):])
    return SOURCES.get(_INDEX.sub("[n]", path))


def _finite(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _walk(cur: Any, parts: list[str], path: str) -> Any:
    """Follow dotted parts; a mapping key may itself contain dots ("system.cpu.load" or "steam.now_playing")."""
    if not parts:
        return cur
    if isinstance(cur, Mapping):
        for n in range(len(parts), 0, -1):
            key = ".".join(parts[:n])
            if key in cur:
                try:
                    return _walk(cur[key], parts[n:], path)
                except KeyError:
                    continue
    raise KeyError(path)


class DataSnapshot(Mapping):
    """Read-only view of host data addressed by dotted paths.

    Accepts nested dicts (`{"weather": {"temp": 3}}`), flat dotted keys (`{"system.cpu.load": 3}`)
    or any mix of the two. Missing or wrongly typed values give None, never an exception.
    `base_dir` resolves relative `path` values (used by the emulator for sample images).
    """

    def __init__(self, data: Mapping | None = None, base_dir=None):
        self._data: Mapping = data if data is not None else {}
        self.base_dir = base_dir

    @property
    def raw(self) -> Mapping:
        """The underlying mapping, e.g. to hand a JSON-serialisable copy to a code-tier screen."""
        return self._data

    def __getitem__(self, path: str):
        cur: Any = self._data
        for seg in re.split(r"(\[\d+\])", path):
            if not seg:
                continue
            if seg[0] == "[":
                i = int(seg[1:-1])
                if not isinstance(cur, (list, tuple)) or i >= len(cur):
                    raise KeyError(path)
                cur = cur[i]
            else:
                cur = _walk(cur, seg.strip(".").split("."), path)
        return cur

    def __iter__(self):
        return iter(self._data)

    def __len__(self):
        return len(self._data)

    def with_settings(self, settings: Mapping) -> DataSnapshot:
        merged = dict(self._data)
        merged["settings"] = dict(settings)
        return DataSnapshot(merged, self.base_dir)

    def value(self, path: str):
        return self.get(path)

    def number(self, path: str) -> float | int | None:
        v = self.get(path)
        return v if _finite(v) else None

    def string(self, path: str) -> str | None:
        v = self.get(path)
        return v if isinstance(v, str) else None

    def series(self, path: str) -> list[float] | None:
        v = self.get(path)
        if not isinstance(v, (list, tuple)):
            return None
        return [x for x in v if _finite(x)]

    def time(self, path: str) -> datetime | None:
        v = self.get(path)
        if isinstance(v, datetime):  # the TimeSource emits aware datetimes
            return v
        try:
            if _finite(v):
                return datetime.fromtimestamp(v, timezone.utc)
            if isinstance(v, str):
                return datetime.fromisoformat(v)
        except (ValueError, OverflowError, OSError):
            pass
        return None


# ---- templates: "CPU {system.cpu.load:.0f}%  {{literal braces}}" ------------------------

_TOKEN = re.compile(r"\{\{|\}\}|\{([A-Za-z0-9_.\[\]]+)(?::([^{}]*))?\}|[{}]")


def parse_template(s: str) -> list[str | tuple[str, str]]:
    """Split into literal strings and (path, spec) refs. ValueError on stray braces."""
    out: list[str | tuple[str, str]] = []
    pos = 0
    for m in _TOKEN.finditer(s):
        if m.start() > pos:
            out.append(s[pos:m.start()])
        tok = m.group(0)
        if tok == "{{":
            out.append("{")
        elif tok == "}}":
            out.append("}")
        elif m.group(1) is not None:
            out.append((m.group(1), m.group(2) or ""))
        else:
            raise ValueError(f"unbalanced '{tok}' at position {m.start()}")
        pos = m.end()
    if pos < len(s):
        out.append(s[pos:])
    return out


def format_value(v: Any, spec: str = "") -> str | None:
    """Format one scalar; None when it cannot be shown."""
    if v is None or isinstance(v, (list, tuple, dict)):
        return None
    try:
        if spec:
            return format(v, spec)
    except (ValueError, TypeError):
        return None
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return format(v, "g") if math.isfinite(v) else None
    return str(v)


def render_template(s: str, snap: DataSnapshot) -> str:
    out = []
    for part in parse_template(s):
        if isinstance(part, str):
            out.append(part)
        else:
            text = format_value(snap.get(part[0]), part[1])
            out.append(PLACEHOLDER if text is None else text)
    return "".join(out)
