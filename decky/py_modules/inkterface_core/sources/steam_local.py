"""Steam data from the local install: box art, playtime, installed-app names, persona.

now_playing is SET by the plugin frontend (SteamClient events) via set_now_playing(appid|None);
this source enriches it with name, box art, session length and total playtime.
"""
from __future__ import annotations

import glob
import os
import time

from .base import Source
from .vdf import VdfError, parse_vdf, vget

ID64_BASE = 76561197960265728  # steamid64 = this + 32-bit account id
BOX_ARTS = ("library_600x900.jpg", "library_capsule.jpg")   # both portrait 2:3; apps without the big one only have the capsule (300x450)
_CANDIDATES = ("~/.local/share/Steam", "~/.steam/steam", "~/.var/app/com.valvesoftware.Steam/.local/share/Steam")


def parse_appid(appid) -> int | None:
    """None -> None. Anything that is not a positive integer raises ValueError (appid reaches file paths)."""
    if appid is None:
        return None
    if isinstance(appid, bool) or not isinstance(appid, (int, str)):
        raise ValueError(f"invalid appid {appid!r}")
    s = str(appid).strip()
    if not (s.isascii() and s.isdigit()) or int(s) <= 0:
        raise ValueError(f"invalid appid {appid!r}")
    return int(s)


def find_steam_dir(home: str | None = None, steam_dir: str | None = None) -> str | None:
    """An explicit steam_dir only has to exist; the default locations must look like a Steam install."""
    if steam_dir:
        return steam_dir if os.path.isdir(steam_dir) else None
    for c in _CANDIDATES:
        c = os.path.join(home or os.path.expanduser("~"), c[2:])
        if any(os.path.isdir(os.path.join(c, d)) for d in ("steamapps", "appcache")):
            return c
    return None


def box_art_path(steam_dir: str, appid: int) -> str | None:
    """Portrait library art, 600x900 first, else the 300x450 capsule (many apps, e.g. Valheim, only have that one).
    Old layout: librarycache/<appid>_<name>. New layout: librarycache/<appid>/<name>, or one hash directory deeper."""
    lc = os.path.join(steam_dir, "appcache", "librarycache")
    sub = os.path.join(lc, str(appid))
    for name in BOX_ARTS:
        for flat in (os.path.join(lc, f"{appid}_{name}"), os.path.join(sub, name)):
            if os.path.isfile(flat):
                return flat
        hits = sorted(glob.glob(os.path.join(glob.escape(sub), "*", name)))
        if hits:
            return hits[0]
    return None


def _int(s, default=0) -> int:
    try:
        return int(s)
    except (TypeError, ValueError):
        return default


class SteamLocal(Source):
    name = "steam_local"
    interval = 10.0
    fast_interval = 2.0  # while now-playing is on screen; polls are stat-cached, a few ms
    instant = True       # local files only: Registry.set_now_playing polls it inline
    paths = ("steam.now_playing", "steam.user", "steam.recent")

    def __init__(self, steam_dir: str | None = None, steamid64: str | None = None, home: str | None = None,
                 clock=time.time, recent_limit: int = 5):
        super().__init__()
        self._explicit_dir = steam_dir
        self._home = home
        self._id64 = str(steamid64) if steamid64 else None
        self._clock = clock
        self._limit = recent_limit
        self._appid: int | None = None
        self._started = 0.0
        self._vdf_cache: dict[str, tuple[float, dict]] = {}
        self._names: dict[int, str] = {}

    def set_now_playing(self, appid) -> None:
        appid = parse_appid(appid)
        if appid != self._appid:
            self._appid = appid
            self._started = self._clock()

    # -- files -----------------------------------------------------------------------------

    def _load(self, path: str) -> dict | None:
        """Parsed VDF, cached by mtime. Steam rewrites these files; a half-written one keeps the previous parse."""
        try:
            mtime = os.stat(path).st_mtime
        except OSError:
            return None
        hit = self._vdf_cache.get(path)
        if hit and hit[0] == mtime:
            return hit[1]
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                data = parse_vdf(f.read())
        except (OSError, VdfError, RecursionError) as e:
            self.warning = f"{os.path.basename(path)}: {type(e).__name__}"
            return hit[1] if hit else None
        self._vdf_cache[path] = (mtime, data)
        return data

    def _libraries(self, root: str) -> list[str]:
        libs = [root]
        for rel in ("steamapps/libraryfolders.vdf", "config/libraryfolders.vdf"):
            lf = self._load(os.path.join(root, rel)) or {}
            folders = vget(lf, "libraryfolders")
            for entry in folders.values() if isinstance(folders, dict) else ():
                p = entry.get("path") if isinstance(entry, dict) else entry if isinstance(entry, str) else None
                if p and p not in libs:
                    libs.append(p)
        return libs

    def _app_name(self, libs: list[str], appid: int) -> str | None:
        if appid in self._names:
            return self._names[appid]
        for lib in libs:
            acf = self._load(os.path.join(lib, "steamapps", f"appmanifest_{appid}.acf"))
            name = vget(acf, "AppState", "name") if acf else None
            if isinstance(name, str) and name:
                self._names[appid] = name
                return name
        return None

    def _persona(self, root: str) -> tuple[str | None, str | None]:
        """(persona name, steamid64) of the most recent Steam login."""
        users = vget(self._load(os.path.join(root, "config", "loginusers.vdf")) or {}, "users") or {}
        users = {sid: u for sid, u in users.items() if isinstance(u, dict)}
        # MostRecent marks the active login; newer Steam builds drop it, then the latest timestamp wins.
        pick = max(users, key=lambda s: (str(users[s].get("MostRecent")) == "1", _int(users[s].get("timestamp"))), default=None)
        return (users[pick].get("PersonaName"), pick) if pick else (None, None)

    def _localconfig(self, root: str, id64: str | None) -> dict:
        path = None
        try:
            path = os.path.join(root, "userdata", str(int(id64) - ID64_BASE), "config", "localconfig.vdf")
        except (TypeError, ValueError):
            pass
        if not path or not os.path.isfile(path):
            found = glob.glob(os.path.join(glob.escape(root), "userdata", "[0-9]*", "config", "localconfig.vdf"))
            path = max(found, key=os.path.getmtime) if found else None
        apps = vget(self._load(path) or {}, "UserLocalConfigStore", "Software", "Valve", "Steam", "apps") if path else None
        out = {}
        for k, v in (apps or {}).items():
            if k.isdigit() and isinstance(v, dict):
                out[int(k)] = {
                    "total": _int(v.get("Playtime")),
                    "w2": _int(v.get("Playtime2wks")),
                    "last": _int(v.get("LastPlayed")),
                }
        return out

    def poll(self) -> dict:
        self.warning = None
        root = find_steam_dir(self._home, self._explicit_dir)
        playing = None
        if root is None:
            self.warning = "Steam directory not found"
            if self._appid:
                playing = {"appid": self._appid, "name": None, "box_art_path": None,
                           "session_minutes": self._session(), "total_minutes": None, "last_achievement": None}
            return {"steam.now_playing": playing, "steam.user": None, "steam.recent": None}

        libs = self._libraries(root)
        persona, login_id = self._persona(root)
        apps = self._localconfig(root, self._id64 or login_id)
        if self._appid:
            a = apps.get(self._appid)
            playing = {
                "appid": self._appid,
                "name": self._app_name(libs, self._appid),
                "box_art_path": box_art_path(root, self._appid),
                "session_minutes": self._session(),
                "total_minutes": a["total"] if a else None,
                "last_achievement": None,
            }
        recent = sorted((i for i, a in apps.items() if a["w2"] > 0), key=lambda i: (-apps[i]["w2"], -apps[i]["last"]))
        return {
            "steam.now_playing": playing,
            "steam.user": {
                "persona": persona,
                "level": None,
                "games_owned": None,
                "hours_total": round(sum(a["total"] for a in apps.values()) / 60, 1) if apps else None,
            },
            "steam.recent": [
                {"appid": i, "name": self._app_name(libs, i), "minutes_2w": apps[i]["w2"], "minutes_total": apps[i]["total"]}
                for i in recent[: self._limit]
            ],
        }

    def _session(self) -> int:
        return max(0, int((self._clock() - self._started) // 60))
