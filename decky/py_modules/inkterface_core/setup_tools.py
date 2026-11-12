"""Helpers behind Settings > Connections: find a place by name, locate by IP, read a Steam account from whatever the user
pastes, show the Steam profile, test the Web API key, read the Steam Machine's Wi-Fi. Stdlib only, no state.

Every function takes the things that touch the outside world as arguments (`fetch(url, timeout) -> bytes`, `run(argv) -> str`),
so tests inject fakes and never reach the network. Every function returns `{"ok": True, ...}` or `{"ok": False, "error": text}`
and never raises. Error text never contains the Steam key or a URL.
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import urllib.parse

from .sources.netfetch import FetchError

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
IP_URL = "https://ipwho.is/?fields=success,message,city,country,country_code,latitude,longitude"   # no key; says "ipwho.is" in the UI
STEAM_API = "https://api.steampowered.com"
COMMUNITY = "https://steamcommunity.com"
IP_PROVIDER = "ipwho.is"
TIMEOUT = 8.0
MAX_AVATAR = 128 * 1024

_ID64_BASE = 76561197960265728          # account id 0 of the public universe, individual, desktop instance
_KEY_RE = re.compile(r"[0-9A-Fa-f]{32}")
_VANITY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{2,31}")
_LANG_RE = re.compile(r"[a-z]{2,3}")
_AVATAR_HOST = re.compile(r"(^|\.)(steamstatic\.com|akamaihd\.net)$")


def _fail(msg: str) -> dict:
    return {"ok": False, "error": msg}


def _clean(text: str) -> bool:
    return not any(ord(c) < 0x20 or ord(c) == 0x7F for c in text)


def _net_error(e: FetchError, what: str) -> str:
    """One line for a failed request; HTTP status first, else the transport text (it never holds the URL)."""
    if e.status is not None:
        if e.status == 429:
            return f"{what} says too many requests. Try again in a minute."
        if e.status >= 500:
            return f"{what} is not answering (HTTP {e.status}). Try again later."
        return f"{what} refused the request (HTTP {e.status})."
    return f"Cannot reach {what}: {e}. Check the Steam Machine's network."


def _get_json(fetch, url: str, what: str):
    """(data, None) or (None, error text)."""
    try:
        raw = fetch(url, TIMEOUT)
    except FetchError as e:
        return None, _net_error(e, what)
    try:
        data = json.loads(raw)
    except ValueError:
        return None, f"{what} sent an answer that is not JSON."
    return (data, None) if isinstance(data, dict) else (None, f"{what} sent an unexpected answer.")


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) != float("inf")


# -- places -------------------------------------------------------------------------------------------------------------

def search_places(fetch, query, language="en") -> dict:
    """Open-Meteo geocoding: `{"ok", "results": [{"label", "name", "lat", "lon", "country_code"}]}`, best match first.
    `label` tells places with the same name apart (name, region, country); `name` is what Settings shows once one is chosen."""
    if not isinstance(query, str) or not _clean(query):
        return _fail("Type a place name.")
    q = " ".join(query.split())
    if len(q) < 2:
        return _fail("Type at least two letters of the place name.")
    if len(q) > 80:
        return _fail("That place name is too long.")
    lang = language if isinstance(language, str) and _LANG_RE.fullmatch(language) else "en"
    url = f"{GEOCODE_URL}?{urllib.parse.urlencode({'name': q, 'count': 8, 'language': lang, 'format': 'json'})}"
    data, err = _get_json(fetch, url, "The place search")
    if err:
        return _fail(err)
    out = []
    for r in data.get("results") or []:
        try:
            name, lat, lon = r["name"], r["latitude"], r["longitude"]
        except (KeyError, TypeError):
            continue
        if not (isinstance(name, str) and name.strip() and _num(lat) and _num(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        country, region = r.get("country"), r.get("admin1")
        short = ", ".join(x.strip() for x in (name, country) if isinstance(x, str) and x.strip() and _clean(x))
        parts = [name, region if isinstance(region, str) and region != name else None, country]
        label = ", ".join(dict.fromkeys(x.strip() for x in parts if isinstance(x, str) and x.strip() and _clean(x)))
        cc = r.get("country_code")
        out.append({"label": label[:120], "name": short[:120], "lat": round(float(lat), 4), "lon": round(float(lon), 4),
                    "country_code": cc.upper() if isinstance(cc, str) and re.fullmatch(r"[A-Za-z]{2}", cc) else None})
    if not out:
        return _fail("No place found with that name. Try the English spelling or a bigger town nearby.")
    return {"ok": True, "results": out}


def locate_by_ip(fetch) -> dict:
    """Approximate position of this connection's public IP from ipwho.is (no key, https). City level at best; rounded to 2 decimals."""
    data, err = _get_json(fetch, IP_URL, IP_PROVIDER)
    if err:
        return _fail(err)
    lat, lon = data.get("latitude"), data.get("longitude")
    if data.get("success") is False or not (_num(lat) and _num(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
        return _fail(f"{IP_PROVIDER} could not place this connection. Search for your town instead.")
    city, country = data.get("city"), data.get("country")
    name = ", ".join(x.strip() for x in (city, country) if isinstance(x, str) and x.strip() and _clean(x))
    cc = data.get("country_code")
    return {"ok": True, "name": name[:120] or "Approximate location", "lat": round(float(lat), 2), "lon": round(float(lon), 2),
            "country_code": cc.upper() if isinstance(cc, str) and re.fullmatch(r"[A-Za-z]{2}", cc) else None, "provider": IP_PROVIDER}


# -- Steam accounts -----------------------------------------------------------------------------------------------------

def valid_id64(n: int) -> bool:
    """A public-universe individual account: universe 1, type 1, instance 1, account id 1 .. 2^32-1."""
    return n >> 56 == 1 and (n >> 52) & 0xF == 1 and (n >> 32) & 0xFFFFF == 1 and n & 0xFFFFFFFF != 0


def _from_account(account: int) -> int | None:
    return _ID64_BASE + account if 0 < account < 2 ** 32 else None


def parse_steam_input(text) -> dict:
    """What the user pasted -> `{"ok", "steamid64"}` or, for a custom name, `{"ok", "vanity"}`.
    Accepts a SteamID64, a profile link (/profiles/<id64>, /id/<name>), SteamID3 ([U:1:n]), SteamID2 (STEAM_0:y:n),
    a 32-bit account number and a bare custom name."""
    bad = _fail("That is not a Steam account. Paste your profile link or the 17-digit SteamID64.")
    if not isinstance(text, str):
        return bad
    t = text.strip()
    if not t or len(t) > 200 or not _clean(t):
        return bad
    if re.fullmatch(r"\d{17}", t):
        return {"ok": True, "steamid64": t} if valid_id64(int(t)) else _fail("That 17-digit number is not a Steam account ID.")
    m = re.fullmatch(r"\[?U:1:(\d{1,10})\]?", t, re.I)
    if m:
        i = _from_account(int(m.group(1)))
        return {"ok": True, "steamid64": str(i)} if i else bad
    m = re.fullmatch(r"STEAM_[0-5]:([01]):(\d{1,10})", t, re.I)
    if m:
        i = _from_account(int(m.group(2)) * 2 + int(m.group(1)))
        return {"ok": True, "steamid64": str(i)} if i else bad
    if re.fullmatch(r"\d{1,10}", t):
        i = _from_account(int(t))
        return {"ok": True, "steamid64": str(i)} if i else bad
    if "/" in t or "." in t:
        u = urllib.parse.urlsplit(t if "//" in t else "//" + t)
        host = (u.hostname or "").lower()
        if u.scheme not in ("", "http", "https") or host not in ("steamcommunity.com", "www.steamcommunity.com"):
            return _fail("Paste a link to your Steam profile (steamcommunity.com/profiles/… or steamcommunity.com/id/…).")
        parts = [urllib.parse.unquote(p) for p in u.path.split("/") if p]
        if len(parts) >= 2 and parts[0] == "profiles":
            return parse_steam_input(parts[1]) if re.fullmatch(r"\d{17}", parts[1]) else bad
        if len(parts) >= 2 and parts[0] == "id" and _VANITY_RE.fullmatch(parts[1]):
            return {"ok": True, "vanity": parts[1]}
        return bad
    if _VANITY_RE.fullmatch(t):
        return {"ok": True, "vanity": t}
    return bad


def resolve_steam_id(fetch, text, api_key: str) -> dict:
    """parse_steam_input, and a custom name is looked up with ISteamUser/ResolveVanityURL (needs the Web API key)."""
    r = parse_steam_input(text)
    if not r["ok"] or "steamid64" in r:
        return r
    key = (api_key or "").strip()
    if not _KEY_RE.fullmatch(key):
        return _fail("A custom profile name can only be looked up with a Steam Web API key. Save the key first, "
                     "or paste the profile link that has the 17-digit number (steamcommunity.com/profiles/…).")
    q = urllib.parse.urlencode({"key": key, "vanityurl": r["vanity"]})
    data, err = _get_json(fetch, f"{STEAM_API}/ISteamUser/ResolveVanityURL/v1/?{q}", "Steam")
    if err:
        return _fail(err.replace(key, "***"))
    resp = data.get("response")
    if not isinstance(resp, dict):
        return _fail("Steam sent an unexpected answer.")
    sid = resp.get("steamid")
    if resp.get("success") == 1 and isinstance(sid, str) and re.fullmatch(r"\d{17}", sid) and valid_id64(int(sid)):
        return {"ok": True, "steamid64": sid}
    return _fail("Steam knows no profile with that custom name.")


def _avatar(fetch, url) -> str | None:
    """The avatar as a data URL, or None. Only Steam's own image hosts; a failure here never fails the profile."""
    if not isinstance(url, str):
        return None
    u = urllib.parse.urlsplit(url)
    if u.scheme != "https" or not _AVATAR_HOST.search(u.hostname or ""):
        return None
    try:
        raw = fetch(url, TIMEOUT)
    except FetchError:
        return None
    if len(raw) > MAX_AVATAR:
        return None
    for magic, mime in ((b"\xff\xd8\xff", "image/jpeg"), (b"\x89PNG\r\n\x1a\n", "image/png"), (b"GIF8", "image/gif")):
        if raw.startswith(magic):
            return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
    return None


def _xml_tag(doc: str, tag: str) -> str | None:
    m = re.search(rf"<{tag}>(?:<!\[CDATA\[(.*?)\]\]>|([^<]*))</{tag}>", doc, re.S)
    return (m.group(1) if m and m.group(1) is not None else m.group(2) if m else None)


def steam_profile(fetch, steamid64, api_key: str = "") -> dict:
    """Persona name and avatar of a Steam account. With the key: GetPlayerSummaries. Without: the public profile's XML page."""
    if not (isinstance(steamid64, str) and re.fullmatch(r"\d{17}", steamid64) and valid_id64(int(steamid64))):
        return _fail("That is not a valid SteamID64.")
    key = (api_key or "").strip()
    if _KEY_RE.fullmatch(key):
        q = urllib.parse.urlencode({"key": key, "steamids": steamid64})
        data, err = _get_json(fetch, f"{STEAM_API}/ISteamUser/GetPlayerSummaries/v2/?{q}", "Steam")
        if err:
            return _fail(err.replace(key, "***"))
        try:
            players = data["response"]["players"]
        except (KeyError, TypeError):
            return _fail("Steam sent an unexpected answer.")
        if not isinstance(players, list) or not players or not isinstance(players[0], dict) or not isinstance(players[0].get("personaname"), str):
            return _fail("Steam knows no profile with that ID.")
        p = players[0]
        return {"ok": True, "steamid64": steamid64, "persona": p["personaname"][:64],
                "avatar": _avatar(fetch, p.get("avatarmedium") or p.get("avatar")), "public": p.get("communityvisibilitystate") == 3}
    try:
        doc = fetch(f"{COMMUNITY}/profiles/{steamid64}?xml=1", TIMEOUT).decode("utf-8", "replace")
    except FetchError as e:
        return _fail(_net_error(e, "Steam"))
    name = _xml_tag(doc, "steamID")
    if not name or not _clean(name):
        return _fail("Steam knows no profile with that ID.")
    return {"ok": True, "steamid64": steamid64, "persona": name.strip()[:64], "avatar": _avatar(fetch, (_xml_tag(doc, "avatarMedium") or "").strip()),
            "public": (_xml_tag(doc, "privacyState") or "").strip().lower() == "public"}


def check_steam_key(fetch, api_key: str, steamid64: str) -> dict:
    """GetPlayerSummaries for the saved account with the saved key, then GetOwnedGames to see whether game details are public.
    `{"ok", "persona", "profile_public", "games_visible"}`; a wrong key, an unknown account and a dead network each get their own message."""
    key = (api_key or "").strip()
    if not _KEY_RE.fullmatch(key):
        return _fail("No Steam Web API key is saved yet.")
    if not (isinstance(steamid64, str) and re.fullmatch(r"\d{17}", steamid64)):
        return _fail("Save your SteamID64 first. The test asks Steam about that account.")
    q = urllib.parse.urlencode({"key": key, "steamids": steamid64})
    try:
        raw = fetch(f"{STEAM_API}/ISteamUser/GetPlayerSummaries/v2/?{q}", TIMEOUT)
    except FetchError as e:
        if e.status in (401, 403):
            return _fail("Steam rejected this key: it is wrong or was revoked. Make a new one at steamcommunity.com/dev/apikey.")
        return _fail(_net_error(e, "Steam"))
    try:
        players = json.loads(raw)["response"]["players"]
    except (ValueError, KeyError, TypeError):
        return _fail("Steam sent an answer that could not be read.")
    if not isinstance(players, list) or not players or not isinstance(players[0], dict):
        return _fail("The key works, but Steam knows no profile with that SteamID64. Check the 17 digits.")
    p = players[0]
    persona = p.get("personaname") if isinstance(p.get("personaname"), str) else "(no name)"
    games = None
    data, err = _get_json(fetch, f"{STEAM_API}/IPlayerService/GetOwnedGames/v1/?{urllib.parse.urlencode({'key': key, 'steamid': steamid64})}", "Steam")
    if not err and isinstance(data.get("response"), dict):
        games = "game_count" in data["response"]   # a private "Game details" setting leaves the answer empty
    return {"ok": True, "persona": persona[:64], "profile_public": p.get("communityvisibilitystate") == 3, "games_visible": games}


# -- the machine's Wi-Fi ------------------------------------------------------------------------------------------------

def _clean_env() -> dict:
    """Decky's PyInstaller process points LD_LIBRARY_PATH at its own bundle, which breaks system tools: give them the original."""
    env = dict(os.environ)
    if "LD_LIBRARY_PATH_ORIG" in env:
        env["LD_LIBRARY_PATH"] = env.pop("LD_LIBRARY_PATH_ORIG")
    else:
        env.pop("LD_LIBRARY_PATH", None)
    env["LC_ALL"] = "C"
    return env


def run_nmcli(argv: list[str]) -> str:
    """nmcli without sudo, no shell, bounded. Raises OSError/subprocess errors; wifi_current turns them into text."""
    r = subprocess.run(["nmcli", *argv], capture_output=True, text=True, timeout=5, env=_clean_env(), check=False)
    if r.returncode != 0:
        raise OSError((r.stderr or r.stdout or f"nmcli exited {r.returncode}").strip()[:120])
    return r.stdout


def _split(line: str) -> list[str]:
    """One line of `nmcli -t`: fields split at ':' with '\\:' and '\\\\' escapes undone."""
    out, cur, i = [], [], 0
    while i < len(line):
        c = line[i]
        if c == "\\" and i + 1 < len(line):
            cur.append(line[i + 1])
            i += 2
            continue
        if c == ":":
            out.append("".join(cur))
            cur = []
        else:
            cur.append(c)
        i += 1
    out.append("".join(cur))
    return out


def _ssid_ok(s: str) -> bool:
    return 0 < len(s.encode("utf-8", "replace")) <= 32 and _clean(s)


def wifi_current(run=run_nmcli) -> dict:
    """The network the Steam Machine is on: `{"ok", "ssid", "saved": [ssid, ...]}`. `ssid` is the active Wi-Fi network, or None when the machine
    is on a cable (then `saved` lists the Wi-Fi networks it remembers, to pick from). Never returns a password: nmcli cannot show one without root."""
    try:
        active = None
        try:
            for line in run(["-t", "-f", "active,ssid", "dev", "wifi"]).splitlines():
                f = _split(line)
                if len(f) == 2 and f[0] == "yes" and _ssid_ok(f[1]):
                    active = f[1]
                    break
        except subprocess.TimeoutExpired:
            raise
        except FileNotFoundError:
            raise
        except OSError:
            pass   # no Wi-Fi device (a cable-only machine): the saved networks below may still exist
        saved: list[str] = []
        if active is None:
            for line in run(["-t", "-f", "name,type", "connection", "show"]).splitlines():
                f = _split(line)
                if len(f) != 2 or f[1] != "802-11-wireless" or len(saved) >= 8:
                    continue
                s = run(["-t", "-g", "802-11-wireless.ssid", "connection", "show", "id", f[0]]).strip()
                if _ssid_ok(s) and s not in saved:
                    saved.append(s)
    except FileNotFoundError:
        return _fail("This machine has no NetworkManager (nmcli), so its Wi-Fi cannot be read. Type the network name.")
    except subprocess.TimeoutExpired:
        return _fail("NetworkManager did not answer. Type the network name.")
    except OSError as e:
        return _fail(f"Cannot read the network: {e}. Type the network name.")
    if active is None and not saved:
        return _fail("This machine has no Wi-Fi network to copy. Type the network name.")
    return {"ok": True, "ssid": active, "saved": saved}
