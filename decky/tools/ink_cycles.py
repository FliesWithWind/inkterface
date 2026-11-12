#!/usr/bin/env python3
"""Drive a real panel with a built-in screen whose values and charts keep changing, using partial refreshes only.

    python3 decky/tools/ink_cycles.py --seconds 300                    # Telemetry, ~1 partial a second
    python3 decky/tools/ink_cycles.py --screens telemetry,thermals-power --seconds 600 --switch-s 20   # A -> B -> A ...
    python3 decky/tools/ink_cycles.py --emulator out/ --seconds 30     # no panel: PNG per refresh in out/

For A/B checks of the partial waveform (docs/partial-refresh.md, "Pixels the window only covers"): flash build A, run this, look at
the glass (white areas and hatched fills next to the updating values, black bars above and below), flash build B, run it again.
The automatic full refresh that clears ghosting is switched off here on purpose, so what the partials leave behind stays visible;
a screen switch still does its full refresh. Prints the engine counters as JSON. Opens the port: nothing else may use the panel.
"""
import argparse
import json
import os
import random
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "py_modules"))

from inkterface_core.emulator import EmulatedPanel  # noqa: E402
from inkterface_core.engine import EmulatedLink, Engine, Settings, load_screens, usb_connect  # noqa: E402

BUILTIN_SCREENS = os.path.join(HERE, "..", "screens")


class Data:
    """Registry stand-in: sample values of the chosen screens, jittered by +-10 % each tick; chart histories scroll."""

    def __init__(self, ids, seed):
        self.d, self.cb, self.rnd = {}, [], random.Random(seed)
        for sid in ids:
            with open(os.path.join(BUILTIN_SCREENS, sid, "sample-data.json")) as f:
                self.d.update(json.load(f))

    def subscribe(self, cb): self.cb.append(cb)
    def unsubscribe(self, cb): self.cb.remove(cb)
    def watch(self, keys): pass
    def set_now_playing(self, appid): pass
    def snapshot(self): return dict(self.d)

    def tick(self):
        for k, v in list(self.d.items()):
            if isinstance(v, list):
                self.d[k] = v[1:] + [max(0.0, v[-1] * self.rnd.uniform(0.9, 1.1))]
            elif isinstance(v, (int, float)):
                self.d[k] = max(0.0, v * self.rnd.uniform(0.9, 1.1))
        for cb in list(self.cb):
            cb()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--screens", default="telemetry", help="comma separated built-in screen ids, shown in turn (default telemetry)")
    ap.add_argument("--seconds", type=float, default=300)
    ap.add_argument("--switch-s", type=int, default=30, help="seconds per screen when several are given (min 5)")
    ap.add_argument("--tick-s", type=float, default=1.0, help="seconds between data changes")
    ap.add_argument("--serial", help="USB serial number substring when several panels are attached")
    ap.add_argument("--emulator", metavar="DIR", help="no panel: write a PNG per REFRESH into DIR")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--keep", action="store_true",
                    help="leave the last frame on the glass: exit without handing the panel back (it shows its own screens after 1 h of silence)")
    args = ap.parse_args(argv)

    ids = [s for s in args.screens.split(",") if s]
    screens, errs = load_screens(BUILTIN_SCREENS)
    if errs or any(i not in screens for i in ids):
        print(f"screens: {errs or 'unknown id'}", file=sys.stderr)
        return 2
    settings = Settings(rotation=ids, rotation_interval_s=max(5, args.switch_s), ghost_clear_minutes=0,
                        idle_timeout_s=3600 if args.keep else 60)
    panel = EmulatedPanel(out_dir=args.emulator) if args.emulator else None
    connect = (lambda: EmulatedLink(panel)) if panel else (lambda: usb_connect(args.serial))
    data = Data(ids, args.seed)
    engine = Engine(data, screens, settings, connect)
    engine.start()
    t0 = time.monotonic()
    try:
        while time.monotonic() - t0 < args.seconds:
            time.sleep(args.tick_s)
            data.tick()
    finally:
        if not args.keep:
            engine.stop()
    st = engine.status()
    print(json.dumps({k: st[k] for k in ("state", "fw_version", "partial_refreshes", "full_refreshes", "naks", "reconnects",
                                          "last_error")} | {"seconds": round(time.monotonic() - t0, 1), "screens": ids}), flush=True)
    if args.keep:
        os._exit(0)  # the engine thread is a daemon: no MODE PANEL, no port close handshake, the glass keeps its picture
    return 0


if __name__ == "__main__":
    sys.exit(main())
