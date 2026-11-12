#!/usr/bin/env python3
"""Hardware test of the sleep hint and the firmware's bus-suspend fallback on a real EE05 (docs/idle-mode.md "Sleep and wake").

    python3 decky/tools/sleep_test.py hint     [--rounds 5] [--serial 7C:4F]
    python3 decky/tools/sleep_test.py fallback [--rounds 3]  [--serial 7C:4F]
    python3 decky/tools/sleep_test.py freeze   [--rounds 2]  [--hold 22] [--serial 7C:4F]

hint      The real engine + the real SleepWatcher + the real stdlib D-Bus client against a *fake* logind
          (decky/tests/fakebus.py: nobody can emit PrepareForSleep for us, and the machine must not suspend). Per round:
          PrepareForSleep(true) -> engine sends MODE PANEL -> panel ACKs -> panel draws its idle screen; then
          PrepareForSleep(false) -> MODE HOST + full redraw. Times (host monotonic clock):
            sleep_ack     signal -> the engine's callback returned (the moment the delay lock is released)
            idle_drawn    signal -> the panel's LOG line "idle ... refresh N ms" (printed when the glass is done)
            resume_active resume signal -> ACK of the full REFRESH (the active screen is on the glass)
fallback  No host hint at all: the host's USB link is interrupted behind the engine's back with USBDEVFS_RESET (the bus
          stops sending SOF for a few hundred ms while the port stays open). Prints the panel's own log lines with its
          millis() stamps: how long the SOF gap was, whether the detector fired, when the idle screen was drawn, and, when
          the engine's heartbeats come back, the unsolicited HELLO that makes the host redraw. A real suspend is a much
          longer gap (the firmware default is 2 s); a bench firmware built with -D INK_BUS_DOWN_MS=150 turns the reset gap
          into a trigger so the whole path can be checked here.
freeze    The wake side of the fallback on the real panel: the engine is frozen (its lock held, port open, no heartbeats) for
          --hold seconds with the panel's silence timeout at 15 s, so the panel goes idle and ends the host session exactly as
          it does after a bus-suspend; when the engine runs again the first heartbeat must make the panel leave idle and send
          the unsolicited HELLO, and the host must redraw. Prints the time from the thaw to the full refresh.
Never opens /dev/ttyACM0/1: the panel is found by USB VID 303a.
"""
import argparse
import fcntl
import json
import logging
import os
import re
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "py_modules"))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))

from fakebus import FakeBus  # noqa: E402

from inkterface_core import dbus_min  # noqa: E402
from inkterface_core import sources as S  # noqa: E402
from inkterface_core import usb_link  # noqa: E402
from inkterface_core.engine import Engine, Settings, load_screens, usb_connect  # noqa: E402
from inkterface_core.sleep_watch import SleepWatcher  # noqa: E402

USBDEVFS_RESET = 0x5514
SCREENS = os.path.join(HERE, "..", "screens")
FAILS: list = []


def ok(cond, label, detail=""):
    print(f"  [{' OK ' if cond else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""), flush=True)
    if not cond:
        FAILS.append(label)
    return cond


class LogTap(logging.Handler):
    """Panel LOG lines (the engine logs them as 'panel: ...') with the host time they were read."""

    def __init__(self):
        super().__init__(logging.INFO)
        self.lines: list[tuple[float, str]] = []

    def emit(self, record):
        msg = record.getMessage()
        if msg.startswith("panel: "):
            self.lines.append((time.monotonic(), msg[7:]))

    def since(self, t0, pattern):
        rx = re.compile(pattern)
        return [(t - t0, m) for t, m in self.lines if t >= t0 and rx.search(m)]


def make_engine(serial, screen="big-clock", **settings):
    screens, errs = load_screens(SCREENS)
    assert not errs, errs
    reg = S.default_registry({}, network=False)
    reg.poll_due(force=True)
    reg.start()
    eng = Engine(reg, screens, Settings(rotation=[screen], rotation_interval_s=3600, **settings), lambda: usb_connect(serial))
    eng.start()
    return eng, reg


def wait(pred, timeout, label):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return ok(False, f"timeout: {label}")


def med(xs):
    return round(statistics.median(xs), 3) if xs else None


def settle(eng):
    assert wait(lambda: eng.status()["state"] == "ready" and eng.status()["full_refreshes"] >= 1, 60, "panel ready with an active screen")
    time.sleep(1.0)


def hint(a, tap):
    bus = FakeBus()
    eng, reg = make_engine(a.serial, a.screen)
    stamps = {}

    def on_sleep():
        r = eng.prepare_sleep()
        stamps["sleep_ack"] = time.monotonic()
        stamps["sleep_ok"] = r
        return r

    watcher = SleepWatcher(on_sleep, eng.wake, bus_factory=lambda: dbus_min.Bus(bus.path, uid=os.geteuid(), timeout=3.0))
    watcher.start()
    res = {"sleep_ack": [], "idle_drawn": [], "resume_mode": [], "resume_active": [], "lock_held_during_callback": []}
    try:
        settle(eng)
        conn = bus.wait_conn()
        wait(lambda: len(conn.locks) >= 1, 5, "delay lock taken")
        ok(watcher.inhibited and conn.locks[0]["args"][3] == "delay", "delay inhibitor held before the signal")
        for i in range(a.rounds):
            print(f"round {i + 1}/{a.rounds}", flush=True)
            st = eng.status()
            full0, lock_n = st["full_refreshes"], len(conn.locks)
            stamps.clear()
            t0 = time.monotonic()
            conn.signal("PrepareForSleep", True)
            wait(lambda: "sleep_ack" in stamps, 8, "engine callback returned")
            ok(stamps.get("sleep_ok") is True, "panel ACKed MODE PANEL", f"{(stamps.get('sleep_ack', t0) - t0) * 1000:.0f} ms")
            res["sleep_ack"].append(stamps["sleep_ack"] - t0)
            wait(lambda: conn.lock_released(lock_n - 1), 8, "lock released")
            ok(conn.lock_released(lock_n - 1), "delay lock released after the ACK (suspend may proceed)")
            wait(lambda: tap.since(t0, r"idle (full|partial|gray4) refresh"), 10, "idle screen drawn")
            drawn = tap.since(t0, r"idle (full|partial|gray4) refresh")
            if drawn:
                res["idle_drawn"].append(drawn[0][0])
                ok(True, "idle screen drawn", f"{drawn[0][0]:.2f} s after the signal: {drawn[0][1]}")
            time.sleep(1.5)
            n_frames = eng.status()["bytes_sent"]
            time.sleep(5.5)   # > the 5 s heartbeat interval: the engine must stay silent while asleep (its failsafe wake is at 20 s)
            ok(eng.status()["bytes_sent"] == n_frames and eng.status()["asleep"], "engine silent while asleep")
            full1 = eng.status()["full_refreshes"]
            t1 = time.monotonic()
            conn.signal("PrepareForSleep", False)
            wait(lambda: len(conn.locks) > lock_n, 5, "delay lock taken again")
            wait(lambda: eng.status()["full_refreshes"] > full1, 15, "full refresh after resume")
            t2 = time.monotonic()
            res["resume_active"].append(t2 - t1)
            ok(eng.status()["full_refreshes"] > full1 and eng.status()["naks"] == 0 and not eng.status()["asleep"],
               "active screen back with a full refresh", f"{t2 - t1:.2f} s after the resume signal")
            ok(len(conn.locks) == lock_n + 1 and not conn.lock_released(-1), "delay lock re-armed")
            del full0
            time.sleep(3.0)
    finally:
        watcher.stop()
        eng.stop()
        reg.stop()
        bus.stop()
    return res


def usb_node(path):
    """/dev/bus/usb/BBB/DDD of the USB device behind /dev/ttyACMn."""
    d = os.path.realpath(os.path.join("/sys/class/tty", os.path.basename(os.path.realpath(path)), "device"))
    while d != "/" and not os.path.exists(os.path.join(d, "idVendor")):
        d = os.path.dirname(d)
    bus, dev = int(open(os.path.join(d, "busnum")).read()), int(open(os.path.join(d, "devnum")).read())
    return f"/dev/bus/usb/{bus:03d}/{dev:03d}"


def usb_reset(node):
    fd = os.open(node, os.O_WRONLY)
    try:
        fcntl.ioctl(fd, USBDEVFS_RESET, 0)
    finally:
        os.close(fd)


def fallback(a, tap):
    eng, reg = make_engine(a.serial, a.screen)
    res = {"rounds": []}
    try:
        settle(eng)
        node = usb_node(usb_link.find_panels(a.serial)[0])
        print(f"panel usb node {node}", flush=True)
        for i in range(a.rounds):
            print(f"round {i + 1}/{a.rounds}", flush=True)
            st0 = eng.status()
            n0 = len(tap.lines)
            t0 = time.monotonic()
            try:
                usb_reset(node)
            except OSError as e:
                ok(False, f"USBDEVFS_RESET failed: {e}")
                break
            print(f"  usb reset done at +{(time.monotonic() - t0) * 1000:.0f} ms", flush=True)
            wait(lambda: len(tap.lines) > n0, 20, "any panel log line after the reset")
            time.sleep(12)   # idle draws (4 s), the engine reconnects/heartbeats, the host redraws
            lines = [(round(t - t0, 3), m) for t, m in tap.lines[n0:]]
            for t, m in lines:
                print(f"    +{t:7.3f}s  {m.rstrip()}")
            st = eng.status()
            rounds = {"lines": lines, "state": st["state"], "reconnects": st["reconnects"] - st0["reconnects"],
                      "full_refreshes": st["full_refreshes"] - st0["full_refreshes"], "naks": st["naks"] - st0["naks"],
                      "last_error": st["last_error"]}
            gap = [int(m.group(1)) for _, mm in lines if (m := re.search(r"SOF gap (\d+) ms", mm))]
            fired = [t for t, mm in lines if "no SOF for" in mm]
            drawn = [t for t, mm in lines if re.search(r"idle (full|partial|gray4) refresh", mm)]
            rounds.update(sof_gap_ms=gap, fired=bool(fired), idle_drawn_s=drawn[:1])
            res["rounds"].append(rounds)
            ok(st["state"] == "ready", "engine back in ready state", f"state={st['state']} error={st['last_error']}")
            time.sleep(5)
    finally:
        eng.stop()
        reg.stop()
    return res


def freeze(a, tap):
    import threading

    eng, reg = make_engine(a.serial, a.screen, idle_timeout_s=15)
    res = {"thaw_to_full_refresh_s": []}
    try:
        settle(eng)
        for i in range(a.rounds):
            print(f"round {i + 1}/{a.rounds}: freezing the engine for {a.hold} s", flush=True)
            n0, st0 = len(tap.lines), eng.status()
            go, held = threading.Event(), threading.Event()

            def hold():
                with eng._lock:   # private on purpose: this is how a frozen host looks to the panel
                    held.set()
                    go.wait()

            t = threading.Thread(target=hold, daemon=True)
            t.start()
            held.wait(5)
            time.sleep(a.hold)
            t0 = time.monotonic()
            go.set()
            t.join(2)
            wait(lambda: eng.status()["full_refreshes"] > st0["full_refreshes"], 20, "full refresh after the thaw")
            dt = time.monotonic() - t0
            res["thaw_to_full_refresh_s"].append(dt)
            time.sleep(0.5)
            st = eng.status()
            for _, m in tap.lines[n0:]:
                print("    ", m.strip().replace("\n", "\n     "))
            ok(st["state"] == "ready" and st["naks"] == st0["naks"], "engine ready, no NAKs", f"state={st['state']}")
            ok(any("idle: enter" in m for _, m in tap.lines[n0:]) and any("idle: leave" in m for _, m in tap.lines[n0:]),
               "panel went idle while frozen and left it on the first frame")
            ok(True, "active screen back after the thaw", f"{dt:.2f} s")
            time.sleep(3)
    finally:
        eng.stop()
        reg.stop()
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["hint", "fallback", "freeze"])
    ap.add_argument("--hold", type=float, default=22.0, help="freeze: seconds the engine stays frozen")
    ap.add_argument("--rounds", type=int, default=5)
    ap.add_argument("--screen", default="big-clock", help="active screen while testing; telemetry keeps the panel refreshing back to back, i.e. inside the DC-DC hold window")
    ap.add_argument("--serial", help="USB serial number fragment (e.g. AA:BB:CC:DD:EE:FF)")
    ap.add_argument("--out", help="write the results as JSON here")
    a = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(name)s %(message)s")
    tap = LogTap()
    logging.getLogger("inkterface_core.engine.engine").setLevel(logging.INFO)
    logging.getLogger("inkterface_core.engine.engine").addHandler(tap)
    res = {"hint": hint, "fallback": fallback, "freeze": freeze}[a.mode](a, tap)
    if a.mode == "hint":
        print("\nmedians over", a.rounds, "rounds:", {k: med(v) for k, v in res.items() if isinstance(v, list) and v and not isinstance(v[0], bool)})
    if a.out:
        with open(a.out, "w") as f:
            json.dump(res, f, indent=2, default=str)
    print(f"RESULT {a.mode}: {'PASS' if not FAILS else 'FAIL ' + str(FAILS)}")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
