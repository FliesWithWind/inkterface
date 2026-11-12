import json
from urllib.parse import parse_qs, urlparse

import pytest
import sourcefs as F

from inkterface_core.sources import SteamWeb
from inkterface_core.sources.base import SourceError
from inkterface_core.sources.netfetch import FetchError

KEY = "ABCDEF0123456789ABCDEF0123456789"
SID = "76561198000000001"

ROUTES = {
    "GetPlayerSummaries": "steam_summaries.json",
    "GetSteamLevel": "steam_level.json",
    "GetOwnedGames": "steam_owned.json",
    "GetRecentlyPlayedGames": "steam_recent.json",
    "GetFriendList": "steam_friendlist.json",
    "GetPlayerAchievements": "steam_achievements.json",
}


class Net:
    """Fake fetch: route by endpoint name -> fixture file | bytes | exception | callable."""

    def __init__(self, **over):
        self.routes = {**ROUTES, **over}
        self.calls = []

    def __call__(self, url, timeout):
        u = urlparse(url)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        self.calls.append((u.path, q, timeout))
        name = next(n for n in self.routes if n in u.path)
        r = self.routes[name]
        if name == "GetPlayerSummaries" and q["steamids"] != SID:  # friends lookup, not the own profile
            r = self.routes.get("FriendSummaries", "steam_friend_summaries.json")
        if isinstance(r, Exception):
            raise r
        if callable(r):
            return r(q)
        return r if isinstance(r, bytes) else F.fx(r)

    def count(self, name):
        return sum(1 for p, _, _ in self.calls if name in p)


def mk(net=None, clock=None, **kw):
    return SteamWeb(KEY, SID, net or Net(), clock=clock or (lambda: 0.0), **kw)


def test_full_snapshot():
    s = mk()
    s.set_now_playing(730)
    r = s.poll()
    assert r["steam.user"] == {"persona": "TestPlayer", "level": 42, "games_owned": 3, "hours_total": round(3000 / 60, 1)}
    assert r["steam.recent"] == [
        {"appid": 730, "name": "Counter-Strike 2", "minutes_2w": 30, "minutes_total": 1500},
        {"appid": 892970, "name": "Valheim", "minutes_2w": 5, "minutes_total": 900}]
    assert r["steam.friends_online"] == 2  # personastate 1 and 3 online, 0 offline
    assert r["steam.now_playing"] == {"appid": 730, "name": "Counter-Strike 2", "total_minutes": 1500,
                                      "last_achievement": "Latest One"}
    assert s.warning is None
    assert set(r) == set(s.paths)


def test_no_key_or_no_steamid_means_all_none_and_no_requests():
    for key, sid in ((None, SID), (KEY, None), ("", ""), ("  ", SID)):
        net = Net()
        r = SteamWeb(key, sid, net).poll()
        assert r == {"steam.now_playing": None, "steam.user": None, "steam.recent": None, "steam.friends_online": None}
        assert net.calls == []


@pytest.mark.parametrize("sid", ["123", "7656119800000000x", "76561198000000001 0", "٧٦٥٦١١٩٨٠٠٠٠٠٠٠٠٠"])
def test_invalid_steamid_is_a_source_error(sid):
    with pytest.raises(SourceError, match="17 digits"):
        SteamWeb(KEY, sid, Net()).poll()


def test_request_shape_and_timeout():
    net = Net()
    s = mk(net, timeout=3.5)
    s.set_now_playing(730)
    s.poll()
    paths = {p for p, _, _ in net.calls}
    assert any(p.endswith("ISteamUser/GetPlayerSummaries/v2/") for p in paths)
    assert all(q["key"] == KEY and t == 3.5 for _, q, t in net.calls)
    owned = next(q for p, q, _ in net.calls if "GetOwnedGames" in p)
    assert owned["steamid"] == SID and owned["include_appinfo"] == "1"
    ach = next(q for p, q, _ in net.calls if "GetPlayerAchievements" in p)
    assert ach["appid"] == "730" and ach["l"] == "en"


def test_key_is_percent_encoded_not_injected():
    net = Net()
    SteamWeb("k&steamid=1", SID, net).poll()
    assert net.calls[0][1]["key"] == "k&steamid=1" and net.calls[0][1]["steamid"] == SID


def test_every_endpoint_failing_raises_without_leaking_key():
    net = Net(**{n: FetchError("HTTP 500", 500) for n in ROUTES})
    with pytest.raises(SourceError) as e:
        mk(net).poll()
    assert KEY not in str(e.value) and "HTTP 500" in str(e.value)


def test_key_redacted_if_a_transport_message_contains_it():
    net = Net(**{n: FetchError(f"boom {KEY}") for n in ROUTES})
    with pytest.raises(SourceError) as e:
        mk(net).poll()
    assert KEY not in str(e.value) and "***" in str(e.value)


def test_partial_failure_keeps_last_good_and_sets_warning():
    net = Net()
    s = mk(net)
    assert s.poll()["steam.user"]["level"] == 42
    net.routes["GetSteamLevel"] = FetchError("HTTP 503", 503)
    net.routes["GetRecentlyPlayedGames"] = b"{not json"
    r = s.poll()
    assert r["steam.user"]["level"] == 42  # last good value carried
    assert r["steam.user"]["persona"] == "TestPlayer"
    assert r["steam.recent"][0]["appid"] == 730
    assert "level: HTTP 503" in s.warning and "recent: malformed JSON" in s.warning
    net.routes["GetSteamLevel"] = "steam_level.json"
    net.routes["GetRecentlyPlayedGames"] = "steam_recent.json"
    s.poll()
    assert s.warning is None


def test_partial_failure_before_any_success_is_none():
    net = Net(GetSteamLevel=FetchError("HTTP 429", 429))
    r = mk(net).poll()
    assert r["steam.user"]["level"] is None and r["steam.user"]["persona"] == "TestPlayer"


@pytest.mark.parametrize("name,body", [
    ("GetPlayerSummaries", b'{"response":{}}'),
    ("GetPlayerSummaries", b'{"response":{"players":[]}}'),
    ("GetSteamLevel", b'{"response":{}}'),
    ("GetOwnedGames", b'{"response":{}}'),
    ("GetRecentlyPlayedGames", b'{"response":{}}'),
    ("GetFriendList", b'{"nope":1}'),
    ("GetOwnedGames", b'[]'),
    ("GetSteamLevel", b'"str"'),
])
def test_unexpected_shapes_never_raise_when_others_work(name, body):
    r = mk(Net(**{name: body})).poll()
    assert set(r) == set(SteamWeb(KEY, SID).paths)


def test_empty_recent_and_owned_games():
    net = Net(GetRecentlyPlayedGames=b'{"response":{"total_count":0}}', GetOwnedGames=b'{"response":{"game_count":0}}')
    r = mk(net).poll()
    assert r["steam.recent"] == []
    assert r["steam.user"]["games_owned"] == 0 and r["steam.user"]["hours_total"] == 0.0


def test_owned_games_cached_for_an_hour():
    t = [0.0]
    net = Net()
    s = mk(net, clock=lambda: t[0])
    s.poll()
    t[0] = 3599
    s.poll()
    assert net.count("GetOwnedGames") == 1
    t[0] = 3601
    s.poll()
    assert net.count("GetOwnedGames") == 2


def test_friends_over_100_are_batched():
    ids = [str(76561198000001000 + i) for i in range(230)]
    friends = json.dumps({"friendslist": {"friends": [{"steamid": i} for i in ids]}}).encode()

    def summaries(q):
        got = q["steamids"].split(",")
        assert len(got) <= 100
        return json.dumps({"response": {"players": [{"steamid": i, "personastate": 1 if n % 2 == 0 else 0}
                                                    for n, i in enumerate(got)]}}).encode()

    net = Net(GetFriendList=friends, FriendSummaries=summaries)
    s = mk(net)
    r = s.poll()
    assert r["steam.friends_online"] == 50 + 50 + 15
    assert net.count("GetPlayerSummaries") == 1 + 3


def test_private_friend_list_is_a_warning_and_none():
    net = Net(GetFriendList=FetchError("HTTP 401", 401))
    s = mk(net)
    r = s.poll()
    assert r["steam.friends_online"] is None and "friends: HTTP 401" in s.warning
    assert r["steam.user"]["level"] == 42


def test_no_friends():
    net = Net(GetFriendList=b'{"friendslist":{"friends":[]}}')
    assert mk(net).poll()["steam.friends_online"] == 0


@pytest.mark.parametrize("status", [400, 403])
def test_achievements_unavailable_is_not_an_error(status):
    net = Net(GetPlayerAchievements=FetchError(f"HTTP {status}", status))
    s = mk(net)
    s.set_now_playing(730)
    r = s.poll()
    assert r["steam.now_playing"]["last_achievement"] is None and s.warning is None


def test_achievements_transport_error_is_a_warning():
    net = Net(GetPlayerAchievements=FetchError("HTTP 500", 500))
    s = mk(net)
    s.set_now_playing(730)
    r = s.poll()
    assert r["steam.now_playing"]["last_achievement"] is None and "achievement: HTTP 500" in s.warning


def test_achievement_without_display_name_falls_back_to_apiname_and_none_achieved():
    only_api = {"playerstats": {"achievements": [{"apiname": "X", "achieved": 1, "unlocktime": 5}]}}
    none = {"playerstats": {"achievements": [{"apiname": "X", "achieved": 0}]}}
    for body, want in ((only_api, "X"), (none, None), ({"playerstats": {}}, None)):
        s = mk(Net(GetPlayerAchievements=json.dumps(body).encode()))
        s.set_now_playing(1)
        assert s.poll()["steam.now_playing"]["last_achievement"] == want


def test_now_playing_none_when_not_playing_and_achievement_reset_on_game_change():
    net = Net()
    s = mk(net)
    assert s.poll()["steam.now_playing"] is None
    assert net.count("GetPlayerAchievements") == 0
    s.set_now_playing(730)
    assert s.poll()["steam.now_playing"]["last_achievement"] == "Latest One"
    s.set_now_playing(892970)
    net.routes["GetPlayerAchievements"] = FetchError("HTTP 500", 500)
    assert s.poll()["steam.now_playing"]["last_achievement"] is None  # not the previous game's
    s.set_now_playing(None)
    assert s.poll()["steam.now_playing"] is None


def test_unknown_game_has_no_name():
    s = mk()
    s.set_now_playing(31337)
    np = s.poll()["steam.now_playing"]
    assert np["appid"] == 31337 and np["name"] is None and np["total_minutes"] is None


def test_invalid_appid_rejected():
    with pytest.raises(ValueError):
        mk().set_now_playing("../x")


def test_recent_limit_is_sent_and_enforced():
    net = Net()
    s = mk(net, recent_limit=1)
    r = s.poll()
    assert len(r["steam.recent"]) == 1
    assert next(q for p, q, _ in net.calls if "GetRecentlyPlayed" in p)["count"] == "1"
