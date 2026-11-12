#!/usr/bin/env python3
"""Hardware test of idle mode on a real EE05 (flashes the e-paper, takes a few minutes).

    python3 decky/tools/idle_test.py [port] [--only clock-weather,...] [--skip-reboot]

For every built-in idle package: compile it, upload it as a store over USB, put the panel in HOST
mode with the 15 s minimum silence timeout, draw a host frame, then go silent. The panel must enter
idle, draw the layout (full refresh; Wi-Fi fetches fail with the example credentials, which must
produce placeholders, not a crash) and, on the first frame the host sends again, hand the display
back. The panel's own LOG lines (queued while nobody listens) give the timings.

Then a store with rotation, a corrupt upload (must NAK BAD_DATA and keep the old store), and a
reboot (DTR/RTS reset) proving the store, the layout and the persisted timeout survive.
Never opens /dev/ttyACM0/1 (game controllers): the port is picked by USB VID 303a or given.
"""
import argparse
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "decky", "py_modules"))

from inkterface_core import proto as P  # noqa: E402
from inkterface_core import usb_link  # noqa: E402
from inkterface_core.idle.compile import compile_layout  # noqa: E402
from inkterface_core.idle.store import pack_store  # noqa: E402
from inkterface_core.idle.upload import upload_store  # noqa: E402

import json  # noqa: E402

SCREENS = os.path.join(ROOT, "decky", "screens")
IDS = ["minimal-clock", "clock-weather", "steam-stats", "clock-weather-gray4"]
FAILS = []
LOGS = []


def ok(cond, label):
    print(f"  [{' OK ' if cond else 'FAIL'}] {label}")
    if not cond:
        FAILS.append(label)
    return cond


def layout(sid):
    scr = json.load(open(os.path.join(SCREENS, sid, "screen.json"), encoding="utf-8"))
    return compile_layout(scr["idle"])


def collect(link, seconds, until=None):
    frames, end = [], time.monotonic() + seconds
    while time.monotonic() < end:
        got = link.poll(0.05)
        frames += got
        if until and until(frames):
            break
    return frames


def request(link, frame, timeout=6.0):
    """send one frame, return the frames received until its ACK/NAK (or timeout)."""
    link.send(frame)
    frames, end = [], time.monotonic() + timeout
    while time.monotonic() < end:
        got = link.poll(0.05)
        frames += got
        if any(f.type in (P.T_ACK, P.T_NAK) for f in got):
            break
    return frames


def acked(frames):
    good = any(f.type == P.T_ACK for f in frames)
    if not good:
        print("    frames received:", [(f.type, f.payload.hex()) for f in frames])
    return good


def hello(link, timeout=40.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        link.send(P.hello_request(link.next_seq()))
        for f in collect(link, 1.0, lambda fr: any(x.type == P.T_HELLO for x in fr)):
            if f.type == P.T_HELLO:
                return P.parse_hello(f.payload)
    return None


def host_frame(link):
    """white screen with a black box, FULL refresh (what the host would send)."""
    fb = bytearray(P.FB_BYTES)
    for y in range(180, 300):
        for x in range(10, 60):
            fb[y * P.STRIDE + x] = 0xFF
    items = []
    for y0 in range(0, P.HEIGHT, 50):
        h = min(50, P.HEIGHT - y0)
        items.append(P.rect(link.next_seq(), 0, y0, P.WIDTH, h, bytes(fb[y0 * P.STRIDE:(y0 + h) * P.STRIDE])))
    for it in items:
        link.send(it)
        collect(link, 0.05)
    frames = request(link, P.refresh(link.next_seq(), full=True), 15.0)
    return acked(frames)


def parse_ms(lines, prefix):
    out = []
    for ln in lines:
        m = re.search(prefix + r".*?refresh (\d+) ms", ln)
        if m:
            out.append(int(m.group(1)))
    return out


def idle_cycle(link, sid, wait_s):
    print(f"\n== {sid}")
    blob = pack_store([layout(sid)], active=0, config={"tz": "CET-1CEST,M3.5.0,M10.5.0/3"})
    t0 = time.monotonic()
    upload_store(link, blob)
    ok(True, f"store uploaded ({len(blob)} bytes, {time.monotonic() - t0:.2f} s, committed to flash)")
    ok(acked(request(link, P.mode_ex(link.next_seq(), P.MODE_HOST, 15, 0))), "MODE HOST, idle timeout 15 s, layout 0")
    ok(host_frame(link), "host frame drawn with a full refresh")
    LOGS.clear()
    print(f"  silent for {wait_s} s (panel should go idle after 15 s and draw)")
    time.sleep(wait_s)
    t0 = time.monotonic()
    frames = request(link, P.heartbeat(link.next_seq()), 8.0)
    types = [f.type for f in frames]
    ok(types[:1] == [P.T_HELLO] and P.T_ACK in types, f"first frame after silence: new session (HELLO) + ACK, answered in {time.monotonic() - t0:.2f} s")
    frames += collect(link, 2.5)
    text = "\n".join(LOGS).splitlines()
    ok(any("idle: enter" in l for l in text), "panel entered idle (LOG: idle: enter)")
    paints = [l for l in text if re.search(r"idle (full|gray4|partial) refresh", l)]
    ok(bool(paints), "panel drew the layout: " + "; ".join(l.strip() for l in paints[:3]))
    ok(any("idle: leave" in l for l in text), "panel left idle on the first frame (LOG: idle: leave)")
    wifi = [l.strip() for l in text if l.startswith("wifi:")]
    print(f"  wifi log: {wifi}")
    for l in text:
        if "heap free" in l:
            print(f"  {l.strip()}")
    full = parse_ms(text, "idle full") or parse_ms(text, "idle gray4")
    if full:
        print(f"  idle first paint: {full[0]} ms")
    # back in host mode: a partial before any full this session is promoted to a full refresh
    collect(link, 0.5)
    LOGS.clear()
    r = request(link, P.rect(link.next_seq(), 0, 0, 8, 8, bytes([0xFF] * 8)))
    ok(acked(r), "RECT accepted after wake (host owns the display again)")
    r = request(link, P.refresh(link.next_seq(), full=False), 15.0)
    ok(acked(r), "REFRESH after idle answered")
    collect(link, 0.5)
    host = [l for l in "\n".join(LOGS).splitlines() if l.startswith("host ")]
    ok(any(l.startswith("host full refresh") for l in host), "first REFRESH after idle was promoted to a full refresh (glass holds the idle picture): " + "; ".join(host))
    return text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("port", nargs="?")
    ap.add_argument("--only")
    ap.add_argument("--skip-reboot", action="store_true")
    a = ap.parse_args()
    path = a.port or (usb_link.find_panels() or [None])[0]
    if not path or not usb_link.is_espressif(path):
        sys.exit("no Espressif (VID 303a) port; pass the by-id path")
    link = usb_link.Link(usb_link.open_port(path), log=lambda t: LOGS.append(t))
    link.ser.reset_input_buffer()
    h = hello(link)
    if not h:
        sys.exit("no HELLO")
    print(f"HELLO: fw {h.fw_ver!r}, caps 0x{h.caps:x}, idle flash free {h.idle_flash_free}")
    ok(h.caps & 3 == 3, "caps: gray4 + idle_storage")
    ok(h.idle_flash_free == 49152, "idle store capacity reported")

    ids = a.only.split(",") if a.only else IDS
    for sid in ids:
        wait = 50 if "gray4" in sid else 32
        idle_cycle(link, sid, wait)

    print("\n== corrupt upload keeps the stored layouts")
    good = pack_store([layout("minimal-clock")], config={"tz": "UTC0"})
    bad = bytearray(good)
    bad[30] ^= 0xFF
    try:
        upload_store(link, bytes(bad))
        ok(False, "corrupt store must be refused")
    except Exception as e:  # noqa: BLE001
        ok("BAD_DATA" in str(e), f"corrupt store refused: {e}")
    ok(acked(request(link, P.mode_ex(link.next_seq(), P.MODE_HOST, 0, 0))), "layout 0 still selectable afterwards")
    ok(not acked(request(link, P.mode_ex(link.next_seq(), P.MODE_HOST, 0, 1), 2.0)), "layout 1 does not exist (one layout stored)")

    print("\n== three layouts with rotation")
    blob = pack_store([layout("minimal-clock"), layout("clock-weather"), layout("steam-stats")], active=1, rotate=True, rotate_s=10)
    upload_store(link, blob)
    ok(True, f"rotation store uploaded ({len(blob)} bytes)")
    for i in range(3):
        ok(acked(request(link, P.mode_ex(link.next_seq(), P.MODE_HOST, 0, i))), f"select layout {i}")
    ok(not acked(request(link, P.mode_ex(link.next_seq(), P.MODE_HOST, 0, 3), 2.0)), "layout 3 does not exist")

    if not a.skip_reboot:
        print("\n== reboot: store + persisted timeout survive")
        ok(acked(request(link, P.mode_ex(link.next_seq(), P.MODE_HOST, 30, 1))), "MODE HOST, timeout 30 s (persisted), layout 1")
        LOGS.clear()
        link.ser.dtr, link.ser.rts = False, True      # EN low: reset the chip
        time.sleep(0.15)
        link.ser.rts = False
        link.ser.reset_input_buffer()
        link.dec = P.Decoder()
        t_reset = time.monotonic()
        time.sleep(33)                                  # no host traffic: idle after 30 s from boot, not after 60 s
        h = hello(link, 20)
        ok(h is not None, "panel answers HELLO after the reboot")
        collect(link, 1.5)
        text = "\n".join(LOGS).splitlines()
        ok(any("loaded store" in l and "3 layout" in l for l in text), "store (3 layouts) loaded from flash after reboot: " + "; ".join(l.strip() for l in text if "store" in l))
        ok(any("idle: enter" in l for l in text), f"idle entered {time.monotonic() - t_reset:.0f} s after boot with the persisted 30 s timeout (default would be 60 s)")

    print("\nrestoring: default timeout 60 s, panel left in pinned idle")
    request(link, P.mode_ex(link.next_seq(), P.MODE_HOST, 60))
    request(link, P.mode(link.next_seq(), P.MODE_PANEL))
    print("\nRESULT:", "ALL OK" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
