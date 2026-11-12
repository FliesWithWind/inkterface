"""Settings > Connections helpers (setup_tools.py). No real network, no real nmcli: every fetch and runner is a fake."""
import json
import subprocess
import urllib.parse

import pytest

from inkterface_core import setup_tools as T
from inkterface_core.sources.netfetch import FetchError

KEY = "ABCDEF0123456789ABCDEF0123456789"
ID64 = "76561198012345678"
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 20


class Net:
    """A fake `fetch`: routes by URL substring, records every URL it was asked for."""

    def __init__(self, routes):
        self.routes, self.urls = routes, []

    def __call__(self, url, timeout):
        self.urls.append(url)
        for part, answer in self.routes.items():
            if part in url:
                if isinstance(answer, Exception):
                    raise answer
                return answer if isinstance(answer, bytes) else json.dumps(answer).encode()
        raise AssertionError(f"unexpected request {url!r}")


def q(url):
    return urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)


# -- places -------------------------------------------------------------------------------------------------------------

KRAKOW = {"results": [
    {"name": "Kraków", "latitude": 50.06143, "longitude": 19.93658, "country": "Poland", "country_code": "PL", "admin1": "Lesser Poland"},
    {"name": "Krakow", "latitude": 44.7, "longitude": -75.1, "country": "United States", "country_code": "US", "admin1": "New York"},
]}


def test_search_places_builds_the_request_and_labels_results():
    net = Net({"geocoding-api.open-meteo.com": KRAKOW})
    r = T.search_places(net, "  Kraków  ", "pl")
    assert r["ok"] and [x["name"] for x in r["results"]] == ["Kraków, Poland", "Krakow, United States"]
    assert r["results"][0] == {"label": "Kraków, Lesser Poland, Poland", "name": "Kraków, Poland", "lat": 50.0614, "lon": 19.9366, "country_code": "PL"}
    url = net.urls[0]
    assert url.startswith("https://geocoding-api.open-meteo.com/v1/search?")
    assert q(url) == {"name": ["Kraków"], "count": ["8"], "language": ["pl"], "format": ["json"]}


@pytest.mark.parametrize("bad", [None, 5, "", " ", "a", "x" * 81, "bad\x00name", "line\nbreak"])
def test_search_places_rejects_a_bad_query_without_a_request(bad):
    net = Net({})
    r = T.search_places(net, bad)
    assert not r["ok"] and r["error"] and net.urls == []


@pytest.mark.parametrize("lang,sent", [("pl", "pl"), ("en", "en"), ("zh-CN", "en"), ("", "en"), (None, "en"), ("E N", "en"), ("deu", "deu")])
def test_search_places_language_is_validated(lang, sent):
    net = Net({"geocoding": KRAKOW})
    T.search_places(net, "Kraków", lang)
    assert q(net.urls[0])["language"] == [sent]


def test_search_places_query_is_url_encoded():
    net = Net({"geocoding": KRAKOW})
    T.search_places(net, "a&b=c #d", "en")
    assert q(net.urls[0])["name"] == ["a&b=c #d"] and "key" not in net.urls[0]


def test_search_places_no_results_is_a_message_not_an_empty_list():
    r = T.search_places(Net({"geocoding": {"generationtime_ms": 0.4}}), "Nowhereville")
    assert not r["ok"] and "No place found" in r["error"]


def test_search_places_skips_malformed_rows():
    rows = {"results": [{"name": "A"}, "junk", {"name": "B", "latitude": 99, "longitude": 0}, {"name": "", "latitude": 1, "longitude": 1},
                        {"name": "C", "latitude": True, "longitude": 1}, {"name": "Ok", "latitude": 1.5, "longitude": 2.5}]}
    r = T.search_places(Net({"geocoding": rows}), "ok")
    assert r["ok"] and [x["name"] for x in r["results"]] == ["Ok"]


def test_search_places_region_equal_to_name_is_not_repeated():
    row = {"results": [{"name": "Berlin", "latitude": 52.5, "longitude": 13.4, "country": "Germany", "admin1": "Berlin"}]}
    assert T.search_places(Net({"geocoding": row}), "Berlin")["results"][0]["label"] == "Berlin, Germany"


@pytest.mark.parametrize("err,expect", [
    (FetchError("HTTP 429", 429), "too many requests"),
    (FetchError("HTTP 503", 503), "not answering"),
    (FetchError("HTTP 404", 404), "refused"),
    (FetchError("URLError: Name or service not known"), "Cannot reach"),
])
def test_search_places_network_errors_are_readable(err, expect):
    r = T.search_places(Net({"geocoding": err}), "Kraków")
    assert not r["ok"] and expect in r["error"] and "https://" not in r["error"]


def test_search_places_not_json_and_not_an_object():
    assert "not JSON" in T.search_places(Net({"geocoding": b"<html>"}), "Kraków")["error"]
    assert "unexpected" in T.search_places(Net({"geocoding": b"[1,2]"}), "Kraków")["error"]


# -- IP location --------------------------------------------------------------------------------------------------------

def test_locate_by_ip_rounds_and_names_the_provider():
    net = Net({"ipwho.is": {"success": True, "city": "Warsaw", "country": "Poland", "country_code": "pl", "latitude": 52.2297666, "longitude": 21.0117787}})
    r = T.locate_by_ip(net)
    assert r == {"ok": True, "name": "Warsaw, Poland", "lat": 52.23, "lon": 21.01, "country_code": "PL", "provider": "ipwho.is"}
    assert net.urls[0].startswith("https://ipwho.is/") and "key" not in net.urls[0]


@pytest.mark.parametrize("answer", [{"success": False, "message": "Reserved range"}, {"success": True}, {"success": True, "latitude": "x", "longitude": 1},
                                    {"success": True, "latitude": 91, "longitude": 1}])
def test_locate_by_ip_refuses_an_unusable_answer(answer):
    r = T.locate_by_ip(Net({"ipwho.is": answer}))
    assert not r["ok"] and "ipwho.is" in r["error"]


def test_locate_by_ip_without_a_city_still_gives_a_name():
    r = T.locate_by_ip(Net({"ipwho.is": {"success": True, "latitude": 1.0, "longitude": 2.0}}))
    assert r["ok"] and r["name"] == "Approximate location"


def test_locate_by_ip_offline():
    r = T.locate_by_ip(Net({"ipwho.is": FetchError("URLError: unreachable")}))
    assert not r["ok"] and "Cannot reach ipwho.is" in r["error"]


# -- Steam account parsing ----------------------------------------------------------------------------------------------

ACCOUNT = 52079950   # SteamID3 [U:1:52079950] == 76561198012345678


@pytest.mark.parametrize("text", [
    ID64, f" {ID64} \n", f"https://steamcommunity.com/profiles/{ID64}", f"https://steamcommunity.com/profiles/{ID64}/",
    f"http://www.steamcommunity.com/profiles/{ID64}/games/?tab=all", f"steamcommunity.com/profiles/{ID64}",
    f"STEAMCOMMUNITY.COM/profiles/{ID64}?x=1#y",
    f"[U:1:{ACCOUNT}]", f"U:1:{ACCOUNT}", f"[u:1:{ACCOUNT}]", str(ACCOUNT),
    f"STEAM_0:{ACCOUNT % 2}:{ACCOUNT // 2}", f"STEAM_1:{ACCOUNT % 2}:{ACCOUNT // 2}",
])
def test_parse_steam_input_to_id64(text):
    assert T.parse_steam_input(text) == {"ok": True, "steamid64": ID64}


@pytest.mark.parametrize("text,vanity", [
    ("https://steamcommunity.com/id/some_name", "some_name"), ("steamcommunity.com/id/some-name/", "some-name"),
    ("https://steamcommunity.com/id/some_name/games", "some_name"), ("some_name", "some_name"), ("abc", "abc"),
])
def test_parse_steam_input_custom_name(text, vanity):
    assert T.parse_steam_input(text) == {"ok": True, "vanity": vanity}


@pytest.mark.parametrize("text", [
    None, 5, "", "   ", "x", "ab", "a" * 33, "a" * 300, "12345678901234567", "76561197960265727", "99999999999999999", "0", "4294967296", "-5",
    "[U:1:0]", "[U:1:4294967296]", "[U:2:5]", "[G:1:5]", "STEAM_0:2:5",
    "https://evil.example/profiles/" + ID64, "https://steamcommunity.com.evil.example/profiles/" + ID64, "https://notsteamcommunity.com/id/x",
    "ftp://steamcommunity.com/id/x", "https://steamcommunity.com/", "https://steamcommunity.com/id/", "https://steamcommunity.com/profiles/123",
    "https://steamcommunity.com/id/bad name", "https://steamcommunity.com/groups/foo", "name with space", "bad\x00", "foo.bar",
])
def test_parse_steam_input_rejects(text):
    r = T.parse_steam_input(text)
    assert r["ok"] is False and r["error"]


def test_valid_id64_checks_universe_type_and_instance():
    assert T.valid_id64(int(ID64))
    assert not T.valid_id64(int(ID64) | (2 << 56))         # another universe
    assert not T.valid_id64(int(ID64) ^ (1 << 52) ^ (3 << 52))   # not an individual
    assert not T.valid_id64(76561197960265728)             # account 0


# -- vanity names -------------------------------------------------------------------------------------------------------

def test_resolve_passes_an_id_through_without_a_request():
    net = Net({})
    assert T.resolve_steam_id(net, ID64, "") == {"ok": True, "steamid64": ID64} and net.urls == []


def test_resolve_vanity_calls_resolvevanityurl_with_the_key():
    net = Net({"ResolveVanityURL": {"response": {"success": 1, "steamid": ID64}}})
    assert T.resolve_steam_id(net, "https://steamcommunity.com/id/some_name/", KEY) == {"ok": True, "steamid64": ID64}
    assert q(net.urls[0]) == {"key": [KEY], "vanityurl": ["some_name"]} and net.urls[0].startswith("https://api.steampowered.com/ISteamUser/ResolveVanityURL/v1/")


@pytest.mark.parametrize("key", ["", "  ", "short", KEY[:-1], KEY + "0", "Z" * 32])
def test_resolve_vanity_needs_a_valid_key_and_makes_no_request(key):
    net = Net({})
    r = T.resolve_steam_id(net, "some_name", key)
    assert not r["ok"] and "Web API key" in r["error"] and net.urls == []


@pytest.mark.parametrize("answer", [{"response": {"success": 42, "message": "No match"}}, {"response": {"success": 1, "steamid": "123"}},
                                    {"response": {"success": 1, "steamid": "12345678901234567"}}, {"response": {"success": 1}}])
def test_resolve_vanity_no_match(answer):
    r = T.resolve_steam_id(Net({"ResolveVanityURL": answer}), "some_name", KEY)
    assert not r["ok"] and "no profile" in r["error"]


def test_resolve_vanity_errors_never_hold_the_key():
    for err in (FetchError(f"HTTP 403 {KEY}", 403), FetchError(f"URLError: {KEY}")):
        r = T.resolve_steam_id(Net({"ResolveVanityURL": err}), "some_name", KEY)
        assert not r["ok"] and KEY not in json.dumps(r)
    r = T.resolve_steam_id(Net({"ResolveVanityURL": {"response": 5}}), "some_name", KEY)
    assert not r["ok"] and KEY not in json.dumps(r)


# -- profile ------------------------------------------------------------------------------------------------------------

SUMMARY = {"response": {"players": [{"steamid": ID64, "personaname": "Ada", "communityvisibilitystate": 3,
                                      "avatarmedium": "https://avatars.steamstatic.com/abc_medium.jpg"}]}}


def test_profile_with_key_uses_getplayersummaries_and_returns_a_data_url_avatar():
    net = Net({"GetPlayerSummaries": SUMMARY, "avatars.steamstatic.com": PNG})
    r = T.steam_profile(net, ID64, KEY)
    assert r["ok"] and r["persona"] == "Ada" and r["public"] is True and r["steamid64"] == ID64
    assert r["avatar"].startswith("data:image/png;base64,")
    assert q(net.urls[0])["steamids"] == [ID64]


def test_profile_without_key_reads_the_public_profile_xml():
    xml = (f"<?xml version='1.0'?><profile><steamID64>{ID64}</steamID64><steamID><![CDATA[Ada & <Co>]]></steamID>"
           "<privacyState>public</privacyState><avatarMedium><![CDATA[https://avatars.akamai.steamstatic.com/x_medium.jpg]]></avatarMedium></profile>")
    net = Net({"/profiles/": xml.encode(), "avatars.akamai.steamstatic.com": b"\xff\xd8\xff\xe0" + b"0" * 10})
    r = T.steam_profile(net, ID64, "")
    assert r["ok"] and r["persona"] == "Ada & <Co>" and r["public"] is True and r["avatar"].startswith("data:image/jpeg;base64,")
    assert net.urls[0] == f"https://steamcommunity.com/profiles/{ID64}?xml=1"
    assert not any("key=" in u for u in net.urls)


def test_profile_private_xml():
    xml = f"<profile><steamID>Bob</steamID><privacyState>private</privacyState></profile>".encode()
    r = T.steam_profile(Net({"/profiles/": xml}), ID64, "")
    assert r["ok"] and r["persona"] == "Bob" and r["public"] is False and r["avatar"] is None


@pytest.mark.parametrize("sid", [None, "", "123", "7656119801234567a", 76561198012345678, "12345678901234567"])
def test_profile_rejects_a_bad_id_without_a_request(sid):
    net = Net({})
    assert not T.steam_profile(net, sid, KEY)["ok"] and net.urls == []


def test_profile_unknown_account():
    assert "no profile" in T.steam_profile(Net({"GetPlayerSummaries": {"response": {"players": []}}}), ID64, KEY)["error"]
    assert "no profile" in T.steam_profile(Net({"/profiles/": b"<response><error>The specified profile could not be found.</error></response>"}), ID64, "")["error"]


def test_profile_avatar_failures_never_fail_the_profile():
    for avatar in ({"avatars.steamstatic.com": FetchError("HTTP 404", 404)}, {"avatars.steamstatic.com": b"<html>not an image</html>"},
                   {"avatars.steamstatic.com": b"\x89PNG\r\n\x1a\n" + b"0" * (T.MAX_AVATAR + 1)}):
        r = T.steam_profile(Net({"GetPlayerSummaries": SUMMARY, **avatar}), ID64, KEY)
        assert r["ok"] and r["persona"] == "Ada" and r["avatar"] is None


@pytest.mark.parametrize("url", ["http://avatars.steamstatic.com/a.jpg", "https://evil.example/a.jpg", "https://steamstatic.com.evil.example/a.jpg",
                                 "https://evilsteamstatic.com/a.jpg", "file:///etc/passwd", "", None, 5])
def test_avatar_is_only_fetched_from_steams_own_hosts_over_https(url):
    summary = {"response": {"players": [{"personaname": "Ada", "avatarmedium": url}]}}
    net = Net({"GetPlayerSummaries": summary})
    r = T.steam_profile(net, ID64, KEY)
    assert r["ok"] and r["avatar"] is None and len(net.urls) == 1


def test_profile_errors_never_hold_the_key():
    r = T.steam_profile(Net({"GetPlayerSummaries": FetchError(f"URLError: {KEY}")}), ID64, KEY)
    assert not r["ok"] and KEY not in json.dumps(r)


# -- key test -----------------------------------------------------------------------------------------------------------

def test_key_test_works_and_reports_game_visibility():
    net = Net({"GetPlayerSummaries": SUMMARY, "GetOwnedGames": {"response": {"game_count": 3, "games": []}}})
    r = T.check_steam_key(net, KEY, ID64)
    assert r == {"ok": True, "persona": "Ada", "profile_public": True, "games_visible": True}
    assert [urllib.parse.urlsplit(u).path for u in net.urls] == ["/ISteamUser/GetPlayerSummaries/v2/", "/IPlayerService/GetOwnedGames/v1/"]
    assert all(q(u)["key"] == [KEY] for u in net.urls)


def test_key_test_private_game_details():
    net = Net({"GetPlayerSummaries": SUMMARY, "GetOwnedGames": {"response": {}}})
    r = T.check_steam_key(net, KEY, ID64)
    assert r["ok"] and r["games_visible"] is False


def test_key_test_private_profile_still_works():
    private = {"response": {"players": [{"personaname": "Ada", "communityvisibilitystate": 1}]}}
    r = T.check_steam_key(Net({"GetPlayerSummaries": private, "GetOwnedGames": {"response": {}}}), KEY, ID64)
    assert r["ok"] and r["profile_public"] is False and r["games_visible"] is False


def test_key_test_games_check_failing_does_not_fail_the_test():
    r = T.check_steam_key(Net({"GetPlayerSummaries": SUMMARY, "GetOwnedGames": FetchError("HTTP 500", 500)}), KEY, ID64)
    assert r["ok"] and r["games_visible"] is None


@pytest.mark.parametrize("status", [401, 403])
def test_key_test_invalid_key(status):
    r = T.check_steam_key(Net({"GetPlayerSummaries": FetchError(f"HTTP {status}", status)}), KEY, ID64)
    assert not r["ok"] and "rejected this key" in r["error"] and KEY not in r["error"]


def test_key_test_unknown_account():
    r = T.check_steam_key(Net({"GetPlayerSummaries": {"response": {"players": []}}}), KEY, ID64)
    assert not r["ok"] and "Steam knows no profile" in r["error"]


def test_key_test_no_network():
    r = T.check_steam_key(Net({"GetPlayerSummaries": FetchError("URLError: [Errno -3] Temporary failure in name resolution")}), KEY, ID64)
    assert not r["ok"] and r["error"].startswith("Cannot reach Steam") and KEY not in r["error"]


@pytest.mark.parametrize("status,expect", [(429, "too many requests"), (500, "not answering"), (404, "refused")])
def test_key_test_other_http_errors(status, expect):
    r = T.check_steam_key(Net({"GetPlayerSummaries": FetchError(f"HTTP {status}", status)}), KEY, ID64)
    assert not r["ok"] and expect in r["error"]


def test_key_test_garbage_answers():
    for body in (b"nope", b"{}", b'{"response": 1}', b'{"response": {"players": 3}}'):
        r = T.check_steam_key(Net({"GetPlayerSummaries": body}), KEY, ID64)
        assert not r["ok"] and r["error"]


@pytest.mark.parametrize("key,sid", [("", ID64), ("nothex" * 6, ID64), (KEY, ""), (KEY, "123"), (KEY, None)])
def test_key_test_needs_both_and_makes_no_request(key, sid):
    net = Net({})
    assert not T.check_steam_key(net, key, sid)["ok"] and net.urls == []


# -- Wi-Fi --------------------------------------------------------------------------------------------------------------

class Nm:
    """A fake nmcli: maps the argv (joined) to its output, or raises."""

    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def __call__(self, argv):
        self.calls.append(argv)
        a = self.answers[" ".join(argv)]
        if isinstance(a, BaseException):
            raise a
        return a


ACTIVE = "-t -f active,ssid dev wifi"
CONNS = "-t -f name,type connection show"


def test_wifi_current_reads_the_active_network():
    nm = Nm({ACTIVE: "no:Other\nyes:Home\\: 5G\nno:Third\n"})
    assert T.wifi_current(nm) == {"ok": True, "ssid": "Home: 5G", "saved": []}
    assert len(nm.calls) == 1


def test_wifi_current_unescapes_backslashes_and_utf8():
    nm = Nm({ACTIVE: "yes:Caf\u00e9\\\\Net\n"})
    assert T.wifi_current(nm)["ssid"] == "Caf\u00e9\\Net"


def test_wifi_current_on_a_cable_lists_saved_networks():
    nm = Nm({ACTIVE: "no:A\nno:B\n", CONNS: "Wired connection 1:802-3-ethernet\nHome profile:802-11-wireless\nOffice:802-11-wireless\nVPN:vpn\n",
             "-t -g 802-11-wireless.ssid connection show id Home profile": "HomeNet\n", "-t -g 802-11-wireless.ssid connection show id Office": "OfficeNet\n"})
    assert T.wifi_current(nm) == {"ok": True, "ssid": None, "saved": ["HomeNet", "OfficeNet"]}


def test_wifi_current_saved_list_is_capped_deduplicated_and_skips_bad_ssids():
    names = [f"p{i}" for i in range(12)]
    answers = {ACTIVE: "", CONNS: "".join(f"{n}:802-11-wireless\n" for n in names)}
    answers.update({f"-t -g 802-11-wireless.ssid connection show id {n}": ("Same\n" if i < 3 else "x" * 33 + "\n" if i == 3 else f"Net{i}\n") for i, n in enumerate(names)})
    r = T.wifi_current(Nm(answers))
    assert r["ok"] and r["saved"][0] == "Same" and len(r["saved"]) <= 8 and r["saved"].count("Same") == 1 and "x" * 33 not in r["saved"]


def test_wifi_current_no_wifi_device_but_saved_networks():
    nm = Nm({ACTIVE: OSError("Error: No Wi-Fi device found."), CONNS: "Home:802-11-wireless\n", "-t -g 802-11-wireless.ssid connection show id Home": "HomeNet\n"})
    assert T.wifi_current(nm) == {"ok": True, "ssid": None, "saved": ["HomeNet"]}


def test_wifi_current_nothing_to_copy():
    r = T.wifi_current(Nm({ACTIVE: "", CONNS: "Wired connection 1:802-3-ethernet\n"}))
    assert not r["ok"] and "no Wi-Fi network to copy" in r["error"]


@pytest.mark.parametrize("exc,expect", [(FileNotFoundError("nmcli"), "no NetworkManager"), (subprocess.TimeoutExpired("nmcli", 5), "did not answer"),
                                        (OSError("Error: NetworkManager is not running."), "NetworkManager is not running")])
def test_wifi_current_failures_are_messages(exc, expect):
    answers = {ACTIVE: exc, CONNS: exc}
    r = T.wifi_current(Nm(answers))
    assert not r["ok"] and expect in r["error"]


def test_wifi_current_ignores_control_characters_and_too_long_ssids():
    r = T.wifi_current(Nm({ACTIVE: "yes:bad\x07name\n", CONNS: ""}))
    assert not r["ok"]
    r = T.wifi_current(Nm({ACTIVE: "yes:" + "n" * 33 + "\n", CONNS: ""}))
    assert not r["ok"]


def test_run_nmcli_uses_no_shell_no_sudo_and_the_original_library_path(monkeypatch):
    seen = {}

    def fake_run(argv, **kw):
        seen.update(argv=argv, **kw)
        return subprocess.CompletedProcess(argv, 0, "yes:Home\n", "")
    monkeypatch.setattr(T.subprocess, "run", fake_run)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/_MEIxyz")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/usr/lib")
    assert T.run_nmcli(["-t", "dev"]) == "yes:Home\n"
    assert seen["argv"] == ["nmcli", "-t", "dev"] and seen["timeout"] == 5 and not seen.get("shell")
    assert seen["env"]["LD_LIBRARY_PATH"] == "/usr/lib" and seen["env"]["LC_ALL"] == "C"
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG")
    T.run_nmcli(["x"])
    assert "LD_LIBRARY_PATH" not in seen["env"]


def test_run_nmcli_failure_raises_oserror_with_nmcli_text(monkeypatch):
    monkeypatch.setattr(T.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 8, "", "Error: NetworkManager is not running.\n"))
    with pytest.raises(OSError, match="NetworkManager is not running"):
        T.run_nmcli(["x"])
