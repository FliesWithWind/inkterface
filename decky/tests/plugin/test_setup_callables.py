"""Settings > Connections callables on the Plugin: {ok,error} never raises, the saved key is used server-side only and never leaves,
the weather place follows its coordinates. Fakes for HTTP and nmcli: no network, no real NetworkManager."""
import json

from inkterface_core.sources.netfetch import FetchError

KEY = "ABCDEF0123456789ABCDEF0123456789"
ID64 = "76561198012345678"
SUMMARY = {"response": {"players": [{"personaname": "Ada", "communityvisibilitystate": 3}]}}
GEO = {"results": [{"name": "Kraków", "latitude": 50.06, "longitude": 19.94, "country": "Poland", "country_code": "PL", "admin1": "Lesser Poland"}]}


def routed(routes, urls=None):
    def fetch(url, timeout):
        if urls is not None:
            urls.append(url)
        for part, answer in routes.items():
            if part in url:
                if isinstance(answer, Exception):
                    raise answer
                return json.dumps(answer).encode()
        raise AssertionError(f"unexpected request {url}")
    return fetch


def test_every_helper_returns_ok_or_error_and_none_raises_without_setup(h):
    """The default harness refuses the network and nmcli: the callables still answer with {ok: False, error}."""
    async def body(p):
        for r in [await p.search_places("Kraków", "en"), await p.locate_by_ip(), await p.resolve_steam_id("some_name"),
                  await p.steam_profile(ID64), await p.test_steam_key(), await p.wifi_current()]:
            assert r["ok"] is False and isinstance(r["error"], str) and r["error"]
    h.run(body)


def test_search_places(h):
    urls = []
    h.use_setup(fetch=routed({"geocoding": GEO}, urls))

    async def body(p):
        r = await p.search_places("Kraków", "pl")
        assert r["ok"] and r["results"][0]["name"] == "Kraków, Poland" and r["results"][0]["lat"] == 50.06
        assert "language=pl" in urls[0]
        assert (await p.search_places("", "pl"))["ok"] is False
        assert (await p.search_places(["x"], "pl"))["ok"] is False
        assert (await p.search_places(None))["ok"] is False
    h.run(body)


def test_locate_by_ip(h):
    h.use_setup(fetch=routed({"ipwho.is": {"success": True, "city": "Warsaw", "country": "Poland", "latitude": 52.23, "longitude": 21.01}}))

    async def body(p):
        r = await p.locate_by_ip()
        assert r["ok"] and r["name"] == "Warsaw, Poland" and r["provider"] == "ipwho.is"
        assert "weather_lat" not in h.host_file() if (h.settings_dir / "host.json").exists() else True   # it only reports, the UI saves
    h.run(body)


def test_resolve_steam_id_uses_the_saved_key_only_on_the_server(h):
    urls = []
    h.use_setup(fetch=routed({"ResolveVanityURL": {"response": {"success": 1, "steamid": ID64}}}, urls))

    async def body(p):
        r = await p.resolve_steam_id("https://steamcommunity.com/id/some_name")
        assert r["ok"] is False and "Web API key" in r["error"] and urls == []    # no key yet: no request
        await p.set_settings({"steam_api_key": KEY})
        r = await p.resolve_steam_id("https://steamcommunity.com/id/some_name")
        assert r == {"ok": True, "steamid64": ID64} and KEY in urls[0]
        assert KEY not in json.dumps(r)
        assert (await p.resolve_steam_id(f"[U:1:52079950]"))["steamid64"] == ID64
        assert (await p.resolve_steam_id(12))["ok"] is False
    h.run(body)


def test_test_steam_key_uses_the_saved_key_and_id(h):
    urls = []
    h.use_setup(fetch=routed({"GetPlayerSummaries": SUMMARY, "GetOwnedGames": {"response": {"game_count": 1}}}, urls))

    async def body(p):
        assert "No Steam Web API key" in (await p.test_steam_key())["error"]
        await p.set_settings({"steam_api_key": KEY})
        assert "SteamID64 first" in (await p.test_steam_key())["error"]
        await p.set_settings({"steamid64": ID64})
        r = await p.test_steam_key()
        assert r == {"ok": True, "persona": "Ada", "profile_public": True, "games_visible": True}
        assert all(KEY in u and ID64 in u for u in urls)
        assert KEY not in json.dumps(r) and KEY not in json.dumps(await p.get_settings())
        assert KEY not in h.caplog.text
        assert not any(KEY in str(a) for _, a in h.events)
    h.run(body)


def test_key_never_appears_in_errors_logs_or_events_when_steam_fails(h):
    h.use_setup(fetch=routed({"Steam": FetchError(f"URLError: echoed {KEY}"), "steampowered": FetchError(f"URLError: echoed {KEY}")}))

    async def body(p):
        await p.set_settings({"steam_api_key": KEY, "steamid64": ID64})
        for r in [await p.test_steam_key(), await p.steam_profile(), await p.resolve_steam_id("some_name")]:
            assert r["ok"] is False and KEY not in json.dumps(r)
        assert KEY not in h.caplog.text and not any(KEY in str(a) for _, a in h.events)
    h.run(body)


def test_invalid_key_message(h):
    h.use_setup(fetch=routed({"GetPlayerSummaries": FetchError("HTTP 403", 403)}))

    async def body(p):
        await p.set_settings({"steam_api_key": KEY, "steamid64": ID64})
        r = await p.test_steam_key()
        assert r["ok"] is False and "rejected this key" in r["error"]
    h.run(body)


def test_steam_profile_defaults_to_the_saved_id_and_checks_an_explicit_one(h):
    urls = []
    h.use_setup(fetch=routed({"GetPlayerSummaries": SUMMARY, "/profiles/": b"x".decode()}, urls))

    async def body(p):
        assert (await p.steam_profile())["ok"] is False                       # nothing saved yet
        await p.set_settings({"steam_api_key": KEY, "steamid64": ID64})
        assert (await p.steam_profile())["persona"] == "Ada" and ID64 in urls[-1]
        assert (await p.steam_profile("76561198000000000"))["ok"] is True and "76561198000000000" in urls[-1]
        assert (await p.steam_profile("nope"))["ok"] is False
    h.run(body)


def test_a_helper_that_blows_up_becomes_an_error_and_is_logged_without_details(h):
    def boom(url, timeout):
        raise RuntimeError(f"secret detail {KEY}")
    h.use_setup(fetch=boom)

    async def body(p):
        await p.set_settings({"steam_api_key": KEY, "steamid64": ID64})
        r = await p.test_steam_key()
        assert r["ok"] is False and KEY not in json.dumps(r) and "secret detail" not in json.dumps(r)
        assert "secret detail" not in h.caplog.text and KEY not in h.caplog.text
        assert "test_steam_key failed: RuntimeError" in h.caplog.text
    h.run(body)


def test_wifi_current(h):
    h.use_setup(wifi=lambda argv: "yes:Home\n" if argv[:3] == ["-t", "-f", "active,ssid"] else "")

    async def body(p):
        r = await p.wifi_current()
        assert r == {"ok": True, "ssid": "Home", "saved": []}
        assert "wifi_ssid" not in (h.host_file() if (h.settings_dir / "host.json").exists() else {})   # a read, nothing saved
    h.run(body)


# -- weather_place ------------------------------------------------------------------------------------------------------

def test_weather_place_is_saved_with_its_coordinates(h):
    async def body(p):
        assert (await p.get_settings())["weather_place"] == ""
        r = await p.set_settings({"weather_lat": 50.06, "weather_lon": 19.94, "weather_place": "Kraków, Poland"})
        assert r["ok"] and r["settings"]["weather_place"] == "Kraków, Poland"
        assert h.host_file()["weather_place"] == "Kraków, Poland"
        assert h.registries[-1].cfg["weather_lat"] == 50.06    # the sources got the coordinates, as before
    h.run(body)


def test_editing_the_coordinates_by_hand_drops_the_place_name(h):
    async def body(p):
        await p.set_settings({"weather_lat": 50.06, "weather_lon": 19.94, "weather_place": "Kraków, Poland"})
        r = await p.set_settings({"weather_lat": 51.0, "weather_lon": 19.94})
        assert r["ok"] and r["settings"]["weather_place"] == ""
        await p.set_settings({"weather_lat": 50.06, "weather_lon": 19.94, "weather_place": "Kraków, Poland"})
        r = await p.set_settings({"weather_lat": None, "weather_lon": None})
        assert r["ok"] and r["settings"]["weather_place"] == "" and r["settings"]["weather_lat"] is None
    h.run(body)


def test_saving_the_same_coordinates_keeps_the_place_name(h):
    async def body(p):
        await p.set_settings({"weather_lat": 50.06, "weather_lon": 19.94, "weather_place": "Kraków, Poland"})
        r = await p.set_settings({"weather_lat": 50.06, "weather_lon": 19.94})
        assert r["settings"]["weather_place"] == "Kraków, Poland"
        r = await p.set_settings({"units": "imperial"})
        assert r["settings"]["weather_place"] == "Kraków, Poland"
    h.run(body)


def test_weather_place_is_validated_and_a_bad_value_changes_nothing(h):
    async def body(p):
        await p.set_settings({"weather_lat": 50.06, "weather_lon": 19.94, "weather_place": "Kraków, Poland"})
        for bad in ("x" * 121, "bad\nname", "bad\x00", 5, ["a"], None):
            r = await p.set_settings({"weather_place": bad})
            assert r["ok"] is False and "weather_place" in r["error"], bad
        assert (await p.get_settings())["weather_place"] == "Kraków, Poland"
    h.run(body)


def test_a_host_file_from_before_weather_place_still_loads(h):
    h.settings_dir.mkdir(parents=True)
    (h.settings_dir / "host.json").write_text(json.dumps({"weather_lat": 1.5, "weather_lon": 2.5, "units": "imperial"}))

    async def body(p):
        s = await p.get_settings()
        assert s["errors"] == [] and s["weather_lat"] == 1.5 and s["weather_place"] == "" and s["units"] == "imperial"
    h.run(body)
