import copy
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import pytest
import sourcefs as F

from inkterface_core.sources import WeatherSource, icon_for
from inkterface_core.sources.base import SourceError
from inkterface_core.sources.netfetch import FetchError, fetch_json, urllib_fetch
from inkterface_core.sources.weather import _WMO

METRIC = json.loads(F.fx("open_meteo_berlin_metric.json"))


def fake(body):
    calls = []

    def f(url, timeout):
        calls.append((url, timeout))
        if isinstance(body, Exception):
            raise body
        return body if isinstance(body, bytes) else json.dumps(body).encode()

    f.calls = calls
    return f


def mk(body=None, **kw):
    f = fake(F.fx("open_meteo_berlin_metric.json") if body is None else body)
    return WeatherSource(kw.pop("lat", 52.52), kw.pop("lon", 13.41), fetch=f, **kw), f


def test_recorded_response_metric():
    s, f = mk()
    w = s.poll()["weather"]
    assert w == {
        "temp": 2.4, "condition": "Overcast", "icon": "cloud", "code": 3, "high": 5.4, "low": 2.3,
        "sunrise": "2026-11-10T07:19", "sunset": "2026-11-10T16:20", "units": "metric",
        "forecast": [
            {"date": "2026-11-11", "high": 5.4, "low": 2.1, "icon": "cloud", "code": 3},
            {"date": "2026-11-12", "high": 5.8, "low": 2.2, "icon": "rain", "code": 53},
            {"date": "2026-11-13", "high": 6.9, "low": 0.9, "icon": "cloud", "code": 3}]}
    q = parse_qs(urlparse(f.calls[0][0]).query)
    assert q["latitude"] == ["52.52"] and q["temperature_unit"] == ["celsius"] and q["forecast_days"] == ["4"]
    assert q["timezone"] == ["auto"] and "weather_code" in q["current"][0]


def test_recorded_response_imperial_requests_fahrenheit():
    s, f = mk(F.fx("open_meteo_berlin_imperial.json"), units="imperial")
    w = s.poll()["weather"]
    assert w["units"] == "imperial" and w["temp"] > 30
    assert parse_qs(urlparse(f.calls[0][0]).query)["temperature_unit"] == ["fahrenheit"]


ENGINE_ICONS = {"sun", "cloud", "rain"}  # icon names of the screen engine that weather may use


def test_icon_for_table():
    assert icon_for(0) == icon_for(1) == "sun"
    assert icon_for(2) == icon_for(3) == icon_for(45) == icon_for(48) == "cloud"
    assert icon_for(61) == icon_for(65) == "rain"
    assert icon_for(73) == icon_for(75) == icon_for(95) == icon_for(99) == "rain"  # snow, thunder -> nearest of three
    assert icon_for(1234) == icon_for(None) == icon_for([1]) == "cloud"


def test_every_wmo_code_maps_to_a_valid_engine_icon():
    documented = {0, 1, 2, 3, 45, 48, 51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 71, 73, 75, 77, 80, 81, 82, 85, 86, 95, 96, 99}
    assert set(_WMO) == documented  # the full Open-Meteo table
    assert {icon_for(c) for c in documented} <= ENGINE_ICONS
    assert {icon for _, icon in _WMO.values()} == ENGINE_ICONS
    assert all(text for text, _ in _WMO.values())


def test_every_code_through_poll_gives_valid_icons_and_raw_code():
    for code in list(_WMO) + [4242]:
        d = copy.deepcopy(METRIC)
        d["current"]["weather_code"] = code
        d["daily"]["weather_code"] = [code] * 4
        w = mk(d)[0].poll()["weather"]
        assert w["icon"] in ENGINE_ICONS and w["code"] == code
        assert all(f["icon"] in ENGINE_ICONS and f["code"] == code for f in w["forecast"])


def test_unknown_code_condition():
    d = copy.deepcopy(METRIC)
    d["current"]["weather_code"] = 4242
    w = mk(d)[0].poll()["weather"]
    assert w["condition"] == "Unknown" and w["icon"] == "cloud" and w["code"] == 4242


def test_unconfigured_returns_none_without_request():
    for lat, lon in ((None, None), (52.5, None), (None, 13.4), ("", "")):
        f = fake(b"{}")
        assert WeatherSource(lat, lon, fetch=f).poll() == {"weather": None}
        assert f.calls == []


@pytest.mark.parametrize("lat,lon", [("abc", 1), (91, 0), (-91, 0), (0, 181), (0, -181), (float("nan"), 0), ([1], 2)])
def test_bad_coordinates_raise_and_do_not_request(lat, lon):
    f = fake(b"{}")
    with pytest.raises(SourceError):
        WeatherSource(lat, lon, fetch=f).poll()
    assert f.calls == []


def test_string_coordinates_from_settings_ok():
    s = WeatherSource("52.52", "13.41", fetch=fake(F.fx("open_meteo_berlin_metric.json")))
    assert s.poll()["weather"]["temp"] == 2.4


def test_bad_units():
    with pytest.raises(SourceError, match="units"):
        WeatherSource(1, 1, "kelvin", fetch=fake(b"{}")).poll()


@pytest.mark.parametrize("err", [FetchError("HTTP 500", 500), FetchError("TimeoutError: timed out"), FetchError("HTTP 429", 429)])
def test_http_errors_and_timeouts_raise_source_error(err):
    with pytest.raises(SourceError) as e:
        mk(err)[0].poll()
    assert e.value.status == err.status


@pytest.mark.parametrize("body", [b"", b"<html>", b"\xff\xfe\x00", b"nan{", b"[]", b"{}", b'{"current":{}, "daily":{}}',
                                  b'{"current":{"temperature_2m":"warm"},"daily":{}}', b'{"current":[], "daily":[]}',
                                  b'{"current":{"temperature_2m":true},"daily":{}}'])
def test_malformed_or_unexpected_payload_raises_source_error(body):
    with pytest.raises(SourceError):
        mk(body)[0].poll()


def test_short_daily_arrays_give_shorter_forecast():
    d = copy.deepcopy(METRIC)
    for k in d["daily"]:
        d["daily"][k] = d["daily"][k][:2]
    w = mk(d)[0].poll()["weather"]
    assert len(w["forecast"]) == 1 and w["forecast"][0]["date"] == "2026-11-11"
    d["daily"] = {"time": ["2026-11-10"]}
    w = mk(d)[0].poll()["weather"]
    assert w["forecast"] == [] and w["high"] is None and w["sunrise"] is None


def test_missing_weather_code_in_forecast():
    d = copy.deepcopy(METRIC)
    del d["daily"]["weather_code"]
    assert mk(d)[0].poll()["weather"]["forecast"][0]["icon"] == "cloud"


# -- netfetch ------------------------------------------------------------------------------------

class _H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/ok":
            body = b'{"a": 1}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/slow":
            import time
            time.sleep(2)
        elif self.path == "/trunc":
            self.send_response(200)
            self.send_header("Content-Length", "100")
            self.end_headers()
            self.wfile.write(b"short")
        elif self.path == "/big":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"x" * (8 * 1024 * 1024 + 10))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def server():
    srv = HTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_urllib_fetch_ok_and_json(server):
    assert urllib_fetch(server + "/ok") == b'{"a": 1}'
    assert fetch_json(urllib_fetch, server + "/ok", 2.0) == {"a": 1}


def test_urllib_fetch_http_error_has_status_and_no_url(server):
    with pytest.raises(FetchError) as e:
        urllib_fetch(server + "/missing?key=SECRET")
    assert e.value.status == 404 and "SECRET" not in str(e.value) and "127.0.0.1" not in str(e.value)
    with pytest.raises(SourceError) as se:
        fetch_json(urllib_fetch, server + "/missing", 2.0)
    assert se.value.status == 404


def test_urllib_fetch_timeout(server):
    with pytest.raises(FetchError) as e:
        urllib_fetch(server + "/slow", timeout=0.3)
    assert e.value.status is None and "timed out" in str(e.value)


def test_urllib_fetch_connection_refused():
    with pytest.raises(FetchError):
        urllib_fetch("http://127.0.0.1:1/x", timeout=1)


def test_truncated_reply_surfaces_as_malformed_json(server):
    with pytest.raises(SourceError, match="malformed JSON"):
        fetch_json(urllib_fetch, server + "/trunc", 2.0)


def test_urllib_fetch_rejects_other_schemes_and_garbage_urls():
    for u in ("file:///etc/passwd", "ftp://x", "javascript:1", "", "http://[bad"):
        with pytest.raises(FetchError):
            urllib_fetch(u)


def test_urllib_fetch_size_cap(server):
    with pytest.raises(FetchError, match="too large"):
        urllib_fetch(server + "/big")
