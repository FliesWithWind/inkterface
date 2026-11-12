"""time.now / time.tz."""
from __future__ import annotations

import datetime
import os

from ..tz import get_zone
from .base import Source


def _local_tzname(localtime_link: str = "/etc/localtime") -> str:
    """IANA name of the system zone ('Europe/Berlin'), else the abbreviation ('CET')."""
    tz = os.environ.get("TZ", "").lstrip(":")
    if tz:
        return tz
    target = os.path.realpath(localtime_link)
    if "zoneinfo/" in target:
        return target.split("zoneinfo/", 1)[1]
    return datetime.datetime.now().astimezone().tzname() or "UTC"


class TimeSource(Source):
    name = "time"
    interval = 1.0
    fast_interval = 0.25  # so a clock shows a new second within 0.25 s; time.now has whole-second resolution
    paths = ("time.now", "time.tz")

    def __init__(self, tz: str | None = None, clock=None):
        """tz: IANA zone from settings, None for the system zone. clock() -> aware datetime (tests)."""
        super().__init__()
        self._clock = clock
        self._zone = None
        self._tzname = None
        if tz:
            self._zone = get_zone(tz)   # stdlib zoneinfo if present, else the system's zone files
            if self._zone is None:
                self.warning = f"timezone {tz!r} unavailable, using system zone"
            else:
                self._tzname = tz

    def poll(self) -> dict:
        now = self._clock() if self._clock else datetime.datetime.now().astimezone()
        if self._zone is not None:
            now = now.astimezone(self._zone)
        return {"time.now": now.replace(microsecond=0), "time.tz": self._tzname or _local_tzname()}
