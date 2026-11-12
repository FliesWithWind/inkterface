"""Optional Steam Web API source. Needs an API key and a steamid64; without them every field is None
and no request is made. Never puts the key into an error message."""
from __future__ import annotations

import time
import urllib.parse

from .base import Source, SourceError
from .netfetch import fetch_json, urllib_fetch
from .steam_local import parse_appid

API = "https://api.steampowered.com"
OWNED_TTL = 3600.0  # the owned-games list is big and changes rarely
_NONE = {"steam.now_playing": None, "steam.user": None, "steam.recent": None, "steam.friends_online": None}


class SteamWeb(Source):
    name = "steam_web"
    interval = 120.0
    paths = tuple(_NONE)

    def __init__(self, api_key: str | None, steamid64: str | None, fetch=urllib_fetch, clock=time.monotonic,
                 timeout: float = 6.0, recent_limit: int = 5):
        super().__init__()
        self._key = (api_key or "").strip()
        self._id = str(steamid64 or "").strip()
        self._fetch = fetch
        self._clock = clock
        self._timeout = timeout
        self._limit = recent_limit
        self._appid: int | None = None
        self._owned: tuple[float, dict] | None = None
        self._last: dict = {}  # last good value per field, reused when one endpoint fails

    def set_now_playing(self, appid) -> None:
        appid = parse_appid(appid)
        if appid != self._appid:
            self._last.pop("achievement", None)  # belongs to the previous game
        self._appid = appid

    # -- requests --------------------------------------------------------------------------

    def _get(self, path: str, **params):
        q = urllib.parse.urlencode({"key": self._key, "steamid": self._id, **params})
        try:
            return fetch_json(self._fetch, f"{API}/{path}?{q}", self._timeout)
        except SourceError as e:
            raise SourceError(str(e).replace(self._key, "***"), e.status) from None

    def _summaries(self, ids) -> list:
        data = self._get("ISteamUser/GetPlayerSummaries/v2/", steamids=",".join(ids))
        return data["response"]["players"]

    def _fetch_owned(self) -> dict:
        if self._owned and self._clock() - self._owned[0] < OWNED_TTL:
            return self._owned[1]
        r = self._get("IPlayerService/GetOwnedGames/v1/", include_appinfo=1, include_played_free_games=1)["response"]
        games = r.get("games") or []
        out = {
            "games_owned": r.get("game_count", len(games)),
            "hours_total": round(sum(g.get("playtime_forever", 0) for g in games) / 60, 1),
            "names": {g["appid"]: g.get("name") for g in games},
            "minutes": {g["appid"]: g.get("playtime_forever") for g in games},
        }
        self._owned = (self._clock(), out)
        return out

    def _friends_online(self) -> int:
        friends = self._get("ISteamUser/GetFriendList/v1/", relationship="friend")["friendslist"]["friends"]
        ids = [f["steamid"] for f in friends]
        return sum(1 for i in range(0, len(ids), 100) for p in self._summaries(ids[i:i + 100]) if p.get("personastate", 0) > 0)

    def _last_achievement(self, appid: int) -> str | None:
        try:
            r = self._get("ISteamUserStats/GetPlayerAchievements/v1/", appid=appid, l="en")
        except SourceError as e:
            if e.status in (400, 403):  # game has no stats, or the profile hides them: not an error
                return None
            raise
        best, name = -1, None
        for a in r.get("playerstats", {}).get("achievements") or []:
            if a.get("achieved") == 1 and a.get("unlocktime", 0) > best:
                best, name = a["unlocktime"], a.get("name") or a.get("apiname")
        return name

    # -- poll ------------------------------------------------------------------------------

    def poll(self) -> dict:
        self.warning = None
        if not self._key or not self._id:
            return dict(_NONE)
        if not (self._id.isascii() and self._id.isdigit() and len(self._id) == 17):
            raise SourceError("steamid64 must be 17 digits")

        errors: list[str] = []
        ok = 0

        def step(field, fn):
            nonlocal ok
            try:
                self._last[field] = fn()
                ok += 1
            except (SourceError, KeyError, TypeError, AttributeError, ValueError) as e:
                errors.append(f"{field}: {e if isinstance(e, SourceError) else 'unexpected response'}")
            return self._last.get(field)

        persona = step("persona", lambda: (self._summaries([self._id]) or [{}])[0].get("personaname"))
        level = step("level", lambda: self._get("IPlayerService/GetSteamLevel/v1/")["response"].get("player_level"))
        owned = step("owned", self._fetch_owned) or {}
        recent = step("recent", lambda: [
            {"appid": g["appid"], "name": g.get("name"), "minutes_2w": g.get("playtime_2weeks", 0),
             "minutes_total": g.get("playtime_forever", 0)}
            for g in (self._get("IPlayerService/GetRecentlyPlayedGames/v1/", count=self._limit)["response"].get("games") or [])
        ][: self._limit])
        friends = step("friends", self._friends_online)
        playing = self._appid
        ach = step("achievement", lambda: self._last_achievement(playing)) if playing else None

        if errors and not ok:
            raise SourceError("; ".join(errors))
        self.warning = "; ".join(errors) or None
        return {
            "steam.now_playing": {
                "appid": playing,
                "name": owned.get("names", {}).get(playing),
                "total_minutes": owned.get("minutes", {}).get(playing),
                "last_achievement": ach,
            } if playing else None,
            "steam.user": {"persona": persona, "level": level, "games_owned": owned.get("games_owned"),
                           "hours_total": owned.get("hours_total")},
            "steam.recent": recent,
            "steam.friends_online": friends,
        }
