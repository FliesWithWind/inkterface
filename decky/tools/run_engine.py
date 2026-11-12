#!/usr/bin/env python3
"""Run the host engine (screens + data sources -> panel). See docs/engine.md.

    python3 decky/tools/run_engine.py --emulator out/ --seconds 30     # no hardware: PNG per refresh in out/
    python3 decky/tools/run_engine.py                                  # real panel (USB VID 303a), Ctrl-C to stop
    python3 decky/tools/run_engine.py --serial 7C:4F --settings s.json

Emulator mode puts the PNG panel behind an in-memory serial link and, by default, makes its
e-paper as slow as the real one (partial 1.8 s, full 4.1 s), so the numbers are realistic.
Sources are local only (time, /proc + hwmon, local Steam) unless --live adds Open-Meteo and
the Steam Web API (key from INKTERFACE_STEAM_KEY). Prints a JSON summary on exit.
Real-panel mode opens the port: only run it if nothing else is using the panel.
"""
import argparse
import json
import logging
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "py_modules"))

from inkterface_core import sources as S  # noqa: E402
from inkterface_core.emulator import EmulatedPanel  # noqa: E402
from inkterface_core.engine import EmulatedLink, Engine, Settings, load_screens, usb_connect  # noqa: E402

BUILTIN_SCREENS = os.path.join(HERE, "..", "screens")


def build(args):
    """(engine, registry, panel or None)."""
    settings, err = Settings.load(args.settings) if args.settings else (Settings(), None)
    if err:
        print(f"settings: {err}", file=sys.stderr)
    screens, errs = load_screens(BUILTIN_SCREENS, *args.screens)
    for e in errs:
        print(f"screen: {e}", file=sys.stderr)
    src = {"weather_lat": args.lat, "weather_lon": args.lon, "units": args.units,
           "steam_api_key": os.environ.get("INKTERFACE_STEAM_KEY"), "steamid64": os.environ.get("INKTERFACE_STEAMID")}
    registry = S.default_registry(src, network=args.live)
    panel = None
    if args.emulator:
        panel = EmulatedPanel(out_dir=args.emulator)
        slow = (0.0, 0.0) if args.no_sim_refresh else (1.8, 4.1)

        def connect():
            return EmulatedLink(panel, partial_s=slow[0], full_s=slow[1])
    else:
        def connect():
            return usb_connect(args.serial)
    engine = Engine(registry, screens, settings, connect, settings_error=err, screen_errors=errs)
    return engine, registry, panel


def summary(engine, panel, seconds, shown):
    st = engine.status()
    out = {k: st[k] for k in ("state", "fw_version", "frames_rendered", "unchanged_ticks", "coalesced", "rects_sent",
                              "bytes_sent", "heartbeats_sent", "partial_refreshes", "full_refreshes", "naks",
                              "reconnects", "last_error")}
    out["seconds"] = round(seconds, 1)
    out["screens_shown"] = shown
    if panel is not None:
        out["panel_refreshes"] = [{"kind": k, "box": list(b), "png": os.path.basename(p) if p else None} for k, b, p in panel.refreshes]
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--emulator", metavar="DIR", help="no panel: write a PNG per REFRESH into DIR")
    ap.add_argument("--no-sim-refresh", action="store_true", help="emulator: instant e-paper refresh")
    ap.add_argument("--serial", help="real panel: pick the USB device whose serial number contains this")
    ap.add_argument("--seconds", type=float, default=0, help="stop after this long (default: until Ctrl-C)")
    ap.add_argument("--settings", help="engine settings JSON (default: built-in defaults)")
    ap.add_argument("--screens", action="append", default=[], metavar="DIR", help="extra screen packages folder (repeatable)")
    ap.add_argument("--live", action="store_true", help="also use the network sources (Open-Meteo, Steam Web API)")
    ap.add_argument("--lat")
    ap.add_argument("--lon")
    ap.add_argument("--units", default="metric", choices=["metric", "imperial"])
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(asctime)s %(name)s %(message)s")

    engine, registry, panel = build(args)
    registry.poll_due(force=True)
    registry.start()
    engine.start()
    t0, shown = time.monotonic(), []
    try:
        while not args.seconds or time.monotonic() - t0 < args.seconds:
            time.sleep(0.25)
            cur = engine.status()["current_screen"]
            if cur and (not shown or shown[-1] != cur):
                shown.append(cur)
    except KeyboardInterrupt:
        pass
    finally:
        engine.stop()
        registry.stop()
    out = summary(engine, panel, time.monotonic() - t0, shown)
    print(json.dumps(out, indent=2))
    return 0 if out["full_refreshes"] + out["partial_refreshes"] > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
