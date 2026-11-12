"""Pluggable host data sources -> one snapshot dict. See docs/data-sources.md.

Not imported by `inkterface_core/__init__`; import `inkterface_core.sources` explicitly.
"""
from __future__ import annotations

import time

from .base import HISTORY_LEN, Registry, Source, SourceError, dumps, jsonable
from .netfetch import FetchError, fetch_json, urllib_fetch
from .steam_local import SteamLocal, box_art_path, parse_appid
from .steam_web import SteamWeb
from .system import SystemSource
from .timesrc import TimeSource
from .weather import WeatherSource, icon_for

__all__ = [
    "HISTORY_LEN", "Registry", "Source", "SourceError", "FetchError", "SystemSource", "TimeSource", "SteamLocal",
    "SteamWeb", "WeatherSource", "box_art_path", "parse_appid", "icon_for", "fetch_json", "urllib_fetch",
    "jsonable", "dumps", "default_registry",
]


def default_registry(settings: dict | None = None, *, fetch=urllib_fetch, root: str = "/", home: str | None = None,
                     clock=time.monotonic, network: bool = True) -> Registry:
    """All sources, in override order (later wins on conflicts: steam_web beats steam_local).

    settings keys (all optional): timezone, steam_dir, steam_api_key, steamid64,
    weather_lat, weather_lon, units ("metric"|"imperial").
    """
    s = settings or {}
    srcs = [TimeSource(s.get("timezone")), SystemSource(root), SteamLocal(s.get("steam_dir"), s.get("steamid64"), home)]
    if network:  # False: offline, no Steam Web API and no weather
        srcs += [SteamWeb(s.get("steam_api_key"), s.get("steamid64"), fetch),
                 WeatherSource(s.get("weather_lat"), s.get("weather_lon"), s.get("units") or "metric", fetch)]
    return Registry(srcs, clock=clock)
