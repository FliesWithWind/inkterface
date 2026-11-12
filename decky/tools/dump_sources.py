#!/usr/bin/env python3
"""Print a real data-source snapshot of this machine as JSON (never opens the panel).

    python3 decky/tools/dump_sources.py                        # time, system, local Steam
    python3 decky/tools/dump_sources.py --live --lat 52.52 --lon 13.41    # + Open-Meteo (network)
    INKTERFACE_STEAM_KEY=... python3 decky/tools/dump_sources.py --live --steamid 7656...   # + Steam Web API

The API key and steamid64 are redacted from the output. Polls the system source twice 1.2 s
apart because CPU load is a delta between two samples.
"""
import argparse
import os
import re
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "py_modules"))

from inkterface_core import sources as S  # noqa: E402


def redact(text: str, secrets) -> str:
    for s in filter(None, secrets):
        text = text.replace(s, "<redacted>")
    return re.sub(r"\b7656119\d{10}\b", "<steamid64>", text)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", action="store_true", help="also use the network sources (Open-Meteo, Steam Web API)")
    ap.add_argument("--lat")
    ap.add_argument("--lon")
    ap.add_argument("--units", default="metric", choices=["metric", "imperial"])
    ap.add_argument("--steamid", default=os.environ.get("INKTERFACE_STEAMID"))
    ap.add_argument("--steam-dir")
    ap.add_argument("--tz")
    ap.add_argument("--now-playing", help="appid to pretend the frontend reported")
    a = ap.parse_args(argv)

    key = os.environ.get("INKTERFACE_STEAM_KEY")
    settings = {"timezone": a.tz, "steam_dir": a.steam_dir, "steamid64": a.steamid, "units": a.units}
    if a.live:
        settings.update(steam_api_key=key, weather_lat=a.lat, weather_lon=a.lon)
    reg = S.default_registry(settings, network=a.live)
    if a.now_playing:
        reg.set_now_playing(a.now_playing)
    reg.poll_due(force=True)
    time.sleep(1.2)
    reg.poll_due(force=True)
    out = S.dumps(reg.snapshot(), indent=2, sort_keys=True)
    print(redact(out, (key, a.steamid)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
