"""Weather from Open-Meteo (no API key)."""
from __future__ import annotations

import urllib.parse

from .base import Source, SourceError
from .netfetch import fetch_json, urllib_fetch

URL = "https://api.open-meteo.com/v1/forecast"
UNITS = {"metric": "celsius", "imperial": "fahrenheit"}

# WMO weather interpretation code -> (condition text, icon). Icons must be names the screen engine
# knows: sun, cloud, rain. Fog -> cloud; drizzle, snow, showers and thunder -> rain (nearest of the three, for now).
_WMO = {
    0: ("Clear sky", "sun"),
    1: ("Mainly clear", "sun"),
    2: ("Partly cloudy", "cloud"),
    3: ("Overcast", "cloud"),
    45: ("Fog", "cloud"),
    48: ("Rime fog", "cloud"),
    51: ("Light drizzle", "rain"),
    53: ("Drizzle", "rain"),
    55: ("Dense drizzle", "rain"),
    56: ("Freezing drizzle", "rain"),
    57: ("Dense freezing drizzle", "rain"),
    61: ("Light rain", "rain"),
    63: ("Rain", "rain"),
    65: ("Heavy rain", "rain"),
    66: ("Freezing rain", "rain"),
    67: ("Heavy freezing rain", "rain"),
    71: ("Light snow", "rain"),
    73: ("Snow", "rain"),
    75: ("Heavy snow", "rain"),
    77: ("Snow grains", "rain"),
    80: ("Light showers", "rain"),
    81: ("Showers", "rain"),
    82: ("Violent showers", "rain"),
    85: ("Snow showers", "rain"),
    86: ("Heavy snow showers", "rain"),
    95: ("Thunderstorm", "rain"),
    96: ("Thunderstorm with hail", "rain"),
    99: ("Thunderstorm with heavy hail", "rain"),
}
ICONS = ("sun", "cloud", "rain")


def icon_for(code) -> str:
    """Engine icon name for a WMO code. Unknown or missing codes give 'cloud' (neutral)."""
    try:
        return _WMO[code][1]
    except (KeyError, TypeError):
        return "cloud"


def _isnum(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


class WeatherSource(Source):
    name = "weather"
    interval = 900.0
    paths = ("weather",)

    def __init__(self, lat=None, lon=None, units: str = "metric", fetch=urllib_fetch, timeout: float = 8.0):
        super().__init__()
        self._lat, self._lon, self._units = lat, lon, units
        self._fetch = fetch
        self._timeout = timeout

    def _coords(self):
        try:
            lat, lon = float(self._lat), float(self._lon)
        except (TypeError, ValueError):
            raise SourceError("invalid latitude/longitude") from None
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):  # also rejects NaN
            raise SourceError("latitude/longitude out of range")
        return lat, lon

    def poll(self) -> dict:
        if self._lat in (None, "") or self._lon in (None, ""):
            return {"weather": None}  # not configured
        if self._units not in UNITS:
            raise SourceError(f"units must be one of {sorted(UNITS)}")
        lat, lon = self._coords()
        q = urllib.parse.urlencode({
            "latitude": lat, "longitude": lon,
            "current": "temperature_2m,weather_code",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,sunrise,sunset",
            "forecast_days": 4, "timezone": "auto", "temperature_unit": UNITS[self._units],
        }, safe=",")
        data = fetch_json(self._fetch, f"{URL}?{q}", self._timeout)
        try:
            cur, daily = data["current"], data["daily"]
            if not _isnum(cur["temperature_2m"]):
                raise TypeError("temperature")
            code = cur.get("weather_code")

            def at(key, i):
                arr = daily.get(key) or []
                return arr[i] if i < len(arr) else None

            forecast = [
                {"date": at("time", i), "high": at("temperature_2m_max", i), "low": at("temperature_2m_min", i),
                 "icon": icon_for(at("weather_code", i)), "code": at("weather_code", i)}
                for i in range(1, 4) if at("time", i) is not None
            ]
            return {"weather": {
                "temp": cur["temperature_2m"],
                "condition": _WMO.get(code, ("Unknown",))[0],
                "icon": icon_for(code),
                "code": code,
                "high": at("temperature_2m_max", 0),
                "low": at("temperature_2m_min", 0),
                "sunrise": at("sunrise", 0),
                "sunset": at("sunset", 0),
                "units": self._units,
                "forecast": forecast,
            }}
        except (KeyError, TypeError, AttributeError):
            raise SourceError("unexpected Open-Meteo response") from None
