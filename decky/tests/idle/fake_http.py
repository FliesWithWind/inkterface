"""A canned HTTP/1.0 fake server for the idle-sources host test.

Spawned by test_sources_host.py as a real `http.server` on 127.0.0.1. Routes
match the endpoints idle_sources.cpp calls (Open-Meteo, Steam Web API, the Steam
store) plus a handful of synthetic routes (/status/404, /json/*, /slow) that
exercise error handling. Every request path+query is appended to the log file
named by $IDLE_REQ_LOG so the C++ test can assert what was (and was not) sent.

Run: python3 fake_http.py <port> <reqlog_path>
"""
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REQ_LOG = None

# --- canned bodies --------------------------------------------------------

WEATHER = {
    "current": {"temperature_2m": 7.5, "weather_code": 3},
    "daily": {
        "time": ["2024-02-29", "2024-03-01", "2024-03-02", "2024-03-03"],
        "temperature_2m_max": [10, 12, 14, 9],
        "temperature_2m_min": [2, 4, 6, 1],
        "sunrise": ["2024-02-29T07:45", "2024-03-01T07:43", "2024-03-02T07:41",
                    "2024-03-03T07:39"],
        "sunset": ["2024-02-29T17:15", "2024-03-01T17:17", "2024-03-02T17:19",
                   "2024-03-03T17:21"],
        "weather_code": [3, 61, 71, 0],
        "precipitation_probability_max": [40, 55, 30, 10],
    },
}

PLAYERS = {"response": {"result": 1, "player_count": 12345}}
USERDATA = [{"data": [[1, 90000], [2, 95000], [3, 98765]]}]
PERSONA = {"response": {"players": [{"personaname": "Gaben"}]}}
LEVEL = {"response": {"player_level": 42}}
OWNED = {"response": {"game_count": 3, "games": [
    {"playtime_forever": 300}, {"playtime_forever": 200}, {"playtime_forever": 100}]}}
RECENT = {"response": {"games": [
    {"name": "Half-Life 3", "appid": 999, "playtime_2weeks": 120, "playtime_forever": 1000},
    {"name": "Portal 3", "appid": 400, "playtime_2weeks": 60, "playtime_forever": 500},
    {"name": "Dota 3", "appid": 570, "playtime_2weeks": 30, "playtime_forever": 9000}]}}
FRIENDLIST = {"friendslist": {"friends": [
    {"steamid": "1"}, {"steamid": "2"}, {"steamid": "3"}]}}
# two online (state!=0), one of them in-game
SUMMARIES = {"response": {"players": [
    {"personastate": 1, "gameid": "570"},
    {"personastate": 3},
    {"personastate": 0}]}}
# achievements for appid 999: two unlocked, latest = "First Blood"
ACHS = {"playerstats": {"achievements": [
    {"name": "First Steps", "achieved": 1, "unlocktime": 100},
    {"name": "First Blood", "achieved": 1, "unlocktime": 500},
    {"name": "Locked", "achieved": 0, "unlocktime": 0}]}}

JSON_DOC = {"a": {"b": [5, 7]}}
JSON_KINDS = {"str": "hi", "b": True, "n": 3.5, "obj": {}, "arr": [1],
              "nul": None, "esc~key": 1, "a": {"b": [9, 7]}}
JSON_LONGSTR = {"s": "x" * 100}
JSON_HUGE = {"x": "y" * 50000}  # > 32768 http_json cap


def _log(path):
    if REQ_LOG:
        with open(REQ_LOG, "a") as f:
            f.write(path + "\n")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *a):  # silence stderr spam
        pass

    def _send(self, code, body_bytes, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body_bytes)))
        self.end_headers()
        self.wfile.write(body_bytes)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj).encode())

    def do_GET(self):
        p = self.path
        _log(p)

        # synthetic error / behaviour routes
        if p.startswith("/status/"):
            code = int(p.split("/")[2])
            self._send(code, b'{"err":1}')
            return
        if p == "/slow":
            time.sleep(2.0)
            self._json({"ok": 1})
            return
        if p.startswith("/json/invalid"):
            self._send(200, b"{not json")
            return
        if p.startswith("/json/huge"):
            self._json(JSON_HUGE)
            return
        if p.startswith("/json/longstr"):
            self._json(JSON_LONGSTR)
            return
        if p.startswith("/json/kinds"):
            self._json(JSON_KINDS)
            return
        if p.startswith("/json/doc"):
            self._json(JSON_DOC)
            return

        # real endpoints (match by path prefix, ignore query)
        if p.startswith("/v1/forecast"):
            self._json(WEATHER)
            return
        if "GetNumberOfCurrentPlayers" in p:
            self._json(PLAYERS)
            return
        if p.startswith("/stats/userdata.json"):
            self._json(USERDATA)
            return
        if "GetPlayerSummaries" in p:
            # friends batch uses a comma-joined steamids list; persona uses one id
            ids = p.split("steamids=")[-1].split("&")[0]
            if "BIGFRIENDS" in p and "," in ids:
                lst = ids.split(",")
                assert len(lst) <= 50, "batches must stay <= 50 ids"
                if "FAIL" in p and "1060" in lst:
                    self._send(500, b"{}")
                    return
                self._json({"response": {"players": [
                    {"personastate": 1, **({"gameid": "7"} if i % 10 == 0 else {})} for i in range(len(lst))]}})
                return
            self._json(SUMMARIES if "," in ids else PERSONA)
            return
        if "GetSteamLevel" in p:
            self._json(LEVEL)
            return
        if "GetOwnedGames" in p:
            self._json(OWNED)
            return
        if "GetRecentlyPlayedGames" in p:
            self._json(RECENT)
            return
        if "GetFriendList" in p:
            if "BIGFRIENDS" in p:
                self._json({"friendslist": {"friends": [{"steamid": str(1000 + i)} for i in range(120)]}})
                return
            self._json(FRIENDLIST)
            return
        if "GetPlayerAchievements" in p:
            self._json(ACHS)
            return

        self._send(404, b'{"err":"no route"}')


def main():
    port = int(sys.argv[1])
    global REQ_LOG
    REQ_LOG = sys.argv[2]
    open(REQ_LOG, "w").close()
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    srv.serve_forever()


if __name__ == "__main__":
    main()
