#!/usr/bin/env python3
"""Hardware end-to-end harness: the real plugin stack (main.py -> PluginCore -> Engine) against a real EE05.

    python3 decky/tools/hw_e2e.py hello
    python3 decky/tools/hw_e2e.py active   [--min-s 120] [--updates 30]
    python3 decky/tools/hw_e2e.py idle
    python3 decky/tools/hw_e2e.py packages
    python3 decky/tools/hw_e2e.py ota
    python3 decky/tools/hw_e2e.py robust
    python3 decky/tools/hw_e2e.py soak     [--minutes 30]
    python3 decky/tools/hw_e2e.py latency  [--stims 30] [--modes live,isolated]
    python3 decky/tools/hw_e2e.py cpu      [--cpu-s 60]
    python3 decky/tools/hw_e2e.py ghost    [--minutes 30] [--ghost-min 10]

It loads `decky/main.py` with a fake `decky` module (the same trick `decky/tests/plugin/conftest.py` uses) but
connects to the real panel through `usb_connect` (USB VID 303a, DTR/RTS low) and uses the live local data sources.
Every step writes `<out>/<step>.json` and prints PASS/FAIL lines; the exit code is 1 if any check failed.

Measurement: the port is wrapped (`TimedPort`). A reader thread timestamps every frame the panel sends the moment it
arrives (not when the engine's 20 ms poll gets round to it) and every frame the engine writes. A *batch* is the RECTs +
REFRESH the engine sends for one render; its latency is `REFRESH ACK arrival - t0`, where t0 is
  * `pipeline`: the `registry.snapshot()` call that produced the batch (what the engine controls: render, diff, USB,
    e-paper);
  * `e2e`: the moment a *stimulus* changed a value on the screen (a displayed number is overwritten in the snapshot at a
    random phase, every 2.5-4 s; everything else on the screen is the live data). This includes waiting for the
    engine's reaction time and for a refresh already in flight to finish. The stimulus is injected into the *source's*
    poll output, so the registry's own sampling delay (`fast_interval` for watched keys) is included; a source that
    learns of a change by an event (Steam: `now-playing`) is polled right after the stimulus instead.
Safety: only ports with VID 303a are opened; ttyACM0/1 (game controllers) are never touched.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import datetime
import importlib.util
import json
import logging
import os
import random
import re
import shutil
import statistics
import struct
import sys
import tempfile
import threading
import time
import types
from pathlib import Path

DECKY = Path(__file__).resolve().parents[1]
REPO = DECKY.parent
sys.path.insert(0, str(DECKY / "py_modules"))

from inkterface_core import proto as P  # noqa: E402
from inkterface_core import usb_link  # noqa: E402
from inkterface_core.engine import usb_connect  # noqa: E402
from inkterface_core.sources import default_registry  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []
APPID = 892970   # Valheim: installed on the dev machine, box art in the Steam librarycache


def check(cond: bool, label: str, detail: str = "") -> bool:
    RESULTS.append((label, bool(cond), detail))
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  {detail}" if detail else ""), flush=True)
    return bool(cond)


def pctl(xs, q):
    xs = sorted(xs)
    if not xs:
        return None
    k = (len(xs) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def stats(xs):
    xs = [x for x in xs if x is not None]
    if not xs:
        return {"n": 0}
    return {"n": len(xs), "min": round(min(xs), 1), "median": round(statistics.median(xs), 1),
            "p90": round(pctl(xs, 0.9), 1), "max": round(max(xs), 1), "mean": round(statistics.fmean(xs), 1)}


def panel_path() -> str:
    ps = usb_link.find_panels()
    assert ps, "no panel (VID 303a) found"
    assert len(ps) == 1, f"several VID 303a ports {ps}: refusing to guess"
    return ps[0]


# ---------------------------------------------------------------------------------------------------------------
# instrumented port + registry
# ---------------------------------------------------------------------------------------------------------------
class TimedPort:
    """SerialPort wrapper: timestamps frames in both directions. The engine reads from a buffer a thread fills."""

    def __init__(self, inner, rig):
        self.inner, self.rig = inner, rig
        self._buf, self._lock, self._err = bytearray(), threading.Lock(), None
        self._stop = threading.Event()
        self._rx, self._tx = P.Decoder(), P.Decoder()
        self._t = threading.Thread(target=self._reader, name="hw-e2e-reader", daemon=True)
        self._t.start()

    def _reader(self):
        while not self._stop.is_set():
            try:
                data = self.inner.read(4096, 0.01)
            except (OSError, ValueError) as e:
                self._err = e
                return
            if data:
                t = time.monotonic()
                for f in self._rx.feed(data):
                    self.rig.on_rx(t, f)
                with self._lock:
                    self._buf += data

    def read(self, size=4096, timeout=None):
        if self._err is not None:
            raise self._err
        with self._lock:
            out = bytes(self._buf[:size])
            del self._buf[:size]
        if not out and timeout:
            time.sleep(min(timeout, 0.01))
        return out

    def write(self, data, timeout=None):
        t = time.monotonic()
        for f in self._tx.feed(bytes(data)):
            self.rig.on_tx(t, f)
        self.inner.write(data, timeout)

    def close(self):
        self._stop.set()
        self._t.join(1.0)
        self.inner.close()


class TapRegistry:
    """Delegates to the real registry; records snapshot times and which stimulus each snapshot already contained."""

    def __init__(self, inner, rig):
        self.inner, self.rig = inner, rig

    def snapshot(self):
        snap = self.inner.snapshot()
        with self.rig.lock:
            v = get_path(snap, self.rig.stim_path) if self.rig.stim_path else None
            sid = self.rig.stim_by_val.get(v, 0) if isinstance(v, (int, float, datetime.datetime)) else 0
            snap.update(copy.deepcopy(self.rig.frozen_hist))
            self.rig.snaps.append((time.monotonic(), sid))
            if len(self.rig.snaps) > 4096:
                del self.rig.snaps[:2048]
        return snap

    def subscribe(self, fn):
        def tapped(*a, **k):
            with self.rig.lock:
                self.rig.notifies.append(time.monotonic())
                if len(self.rig.notifies) > 4096:
                    del self.rig.notifies[:2048]
            return fn(*a, **k)
        return self.inner.subscribe(tapped)

    def __getattr__(self, name):
        return getattr(self.inner, name)


def set_path(path):
    """Overwrite `path` in a source fragment (or snapshot); 'steam.now_playing.session_minutes' edits the nested dict.
    Returns False when the fragment does not hold that key (a source that has nothing to say about it right now)."""
    def fn(frag, value):
        if path in frag:
            frag[path] = value
            return True
        parts = path.split(".")
        for i in range(1, len(parts)):
            head = ".".join(parts[:i])
            if isinstance(frag.get(head), dict):
                frag[head] = {**frag[head], ".".join(parts[i:]): value}
                return True
        return False
    return fn


def get_path(snap, path):
    if path in snap:
        return snap[path]
    parts = path.split(".")
    for i in range(1, len(parts)):
        head = ".".join(parts[:i])
        if isinstance(snap.get(head), dict):
            return snap[head].get(".".join(parts[i:]))
    return None


STIM = {   # screen id -> (overlay path, value generator, source to poll right away or None)
    "telemetry": ("system.cpu.load", lambda: round(random.uniform(5, 95), 1), None),
    "thermals-power": ("system.power.package_w", lambda: round(random.uniform(1, 28), 1), None),
    "now-playing": ("steam.now_playing.session_minutes", lambda: random.randint(1, 9999), "steam_local"),
    "big-clock": ("time.now", lambda: datetime.datetime(2026, 1, 1, random.randint(0, 23), random.randint(0, 59), 0).astimezone(), None),
}


# ---------------------------------------------------------------------------------------------------------------
# the rig
# ---------------------------------------------------------------------------------------------------------------
class Rig:
    """The plugin (main.py) running on its own asyncio loop in a thread, panel = the real one."""

    def __init__(self, out: Path, state: Path | None = None, connect=None, firmware_kw=None, wire_log=False):
        self.out = out
        out.mkdir(parents=True, exist_ok=True)
        self.tmp = Path(tempfile.mkdtemp(prefix="hw-e2e-"))
        self.settings_dir = state or self.tmp / "settings"
        self.plugin_dir = self.tmp / "plugin"
        self.plugin_dir.mkdir()
        (self.plugin_dir / "screens").symlink_to(DECKY / "screens")
        shutil.copy(DECKY / "package.json", self.plugin_dir / "package.json")
        self.lock = threading.RLock()
        self.t0 = time.monotonic()
        self.events: list = []
        self.logs: list[tuple[float, str]] = []
        self.batches: list[dict] = []
        self.cur: dict | None = None
        self.tx_types: dict[int, int] = {}
        self.naks: list = []
        self.hellos = 0
        self.snaps: list = []
        self.notifies: list = []   # when the registry told the engine a watched value changed
        # stimuli are injected into the SOURCES' poll output (so the registry's own sampling delay is part of the
        # measurement): overlay = {path: value} being "reported by the source", frozen = {path: constant}
        self.stim_id, self.overlay, self.frozen, self.stims = 0, {}, {}, []
        self.frozen_hist = {}   # history series pinned too, or a sparkline keeps shifting while the values are frozen
        self.stim_path, self.stim_by_val = None, {}
        self.opens: list[float] = []
        self.last_tx = 0.0
        self.begin_seqs: set[int] = set()
        self.begin_acks: list[int] = []
        self._lbuf = ""
        self.plines: list[tuple[float, int | None, str]] = []   # (host arrival, panel millis, text) per complete log line
        self.fb = bytearray(P.FB_BYTES)   # the panel's shadow buffer (1 = black), rebuilt from the RECTs we sent
        self.ports: list[TimedPort] = []
        self.wire_log = wire_log
        self._connect_fn = connect
        self.firmware_kw = firmware_kw or {}
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, name="hw-e2e-loop", daemon=True)
        self.thread.start()
        self.plugin = None
        self.mod = None
        self._stim_stop = threading.Event()
        self._stim_thread = None
        self.logger = logging.getLogger("hw-e2e")
        self.logger.setLevel(logging.INFO)

    # -- wire callbacks ---------------------------------------------------------------------------------------
    def on_tx(self, t, f):
        with self.lock:
            self.last_tx = t
            self.tx_types[f.type] = self.tx_types.get(f.type, 0) + 1
            if f.type == P.T_OTA_BEGIN:
                self.begin_seqs.add(f.seq)
            else:
                self.begin_seqs.discard(f.seq)   # seq numbers wrap: only the newest frame with this seq counts
            if f.type == P.T_RECT:
                x, y, w, h = struct.unpack_from("<HHHH", f.payload)
                for r in range(h):
                    o = (y + r) * P.STRIDE + x // 8
                    self.fb[o:o + w // 8] = f.payload[8 + r * (w // 8):8 + (r + 1) * (w // 8)]
                if self.cur is None:
                    self.cur = {"t_first": t, "rects": 0, "bytes": 0, "t_refresh": None, "seq": None, "full": None,
                                "t_ack": None, "stim": None, "t_snap": None}
                    s = [x for x in self.snaps if x[0] <= t]
                    if s:
                        self.cur["t_snap"], self.cur["stim"] = s[-1]
                self.cur["rects"] += 1
                self.cur["bytes"] += len(f.payload) + 10
            elif f.type == P.T_REFRESH:
                full = bool(f.payload and f.payload[0])
                b = self.cur or {"t_first": t, "rects": 0, "bytes": 0, "t_snap": None, "stim": None}
                b.update(t_refresh=t, seq=f.seq, full=full, t_ack=None)
                if b["t_snap"] is None:
                    s = [x for x in self.snaps if x[0] <= t]
                    if s:
                        b["t_snap"], b["stim"] = s[-1]
                self.batches.append(b)
                self.cur = None

    def on_rx(self, t, f):
        with self.lock:
            if f.type == P.T_ACK and f.payload:
                if f.payload[0] in self.begin_seqs and len(f.payload) >= 5:
                    self.begin_acks.append(struct.unpack_from("<I", f.payload, 1)[0])   # bytes the panel already holds
                for b in reversed(self.batches[-4:]):
                    if b["seq"] == f.payload[0] and b["t_ack"] is None:
                        b["t_ack"] = t
                        break
            elif f.type == P.T_NAK and len(f.payload) >= 2:
                self.naks.append((t, f.payload[0], P.NAK_NAMES.get(f.payload[1], f.payload[1])))
            elif f.type == P.T_LOG:
                raw = f.payload.decode("utf-8", "replace")
                txt = raw.rstrip()
                self.logs.append((t, txt))
                self._lbuf += raw
                while "\n" in self._lbuf:
                    line, self._lbuf = self._lbuf.split("\n", 1)
                    m = re.match(r"\[(\d+)\] (.*)$", line.rstrip("\r"))
                    self.plines.append((t, int(m.group(1)) if m else None, m.group(2) if m else line.rstrip("\r")))
                if self.wire_log:
                    print(f"      panel: {txt}", flush=True)
            elif f.type == P.T_HELLO:
                self.hellos += 1

    # -- plugin plumbing --------------------------------------------------------------------------------------
    def connect(self):
        if self._connect_fn is not None:
            return self._connect_fn()
        raw = usb_connect()
        self.opens.append(time.monotonic())
        p = TimedPort(raw, self)
        self.ports.append(p)
        return p

    def _load_main(self):
        d = types.ModuleType("decky")
        d.logger = self.logger
        d.DECKY_PLUGIN_SETTINGS_DIR = str(self.settings_dir)
        d.DECKY_PLUGIN_DIR = str(self.plugin_dir)

        async def emit(event, *args):
            self.events.append((time.monotonic(), event, args))

        d.emit = emit
        sys.modules["decky"] = d
        spec = importlib.util.spec_from_file_location("plugin_main", DECKY / "main.py")
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)
        self.mod.connect = self.connect
        self.mod.registry_factory = self.make_registry
        self.mod.WATCH_S = 0.5
        self.mod.firmware_kw = self.firmware_kw

    def make_registry(self, settings):
        reg = default_registry(settings)
        for slot in reg._slots:
            self._wrap(slot.source)
        return TapRegistry(reg, self)

    def _wrap(self, src):
        orig = src.poll

        def poll():
            frag = orig()
            with self.lock:
                items = [*self.frozen.items(), *self.overlay.items()]
            for path, v in items:
                set_path(path)(frag, v)
            return frag

        src.poll = poll

    def push(self, source):
        """Poll `source` now (a source that learned of a change by an event, not by its timer)."""
        reg = self.engine.registry.inner
        for slot in reg._slots:
            if slot.source.name == source:
                reg._poll(slot)

    def run(self, coro, timeout=120):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def start(self, wait_connected=True, timeout=60):
        self._load_main()
        self.plugin = self.mod.Plugin()
        self.run(self.plugin._main())
        if wait_connected:
            self.until(lambda: self.engine.status()["connected"], timeout, "panel connected")

    def stop(self):
        self._stim_stop.set()
        try:
            if self.plugin is not None:
                self.run(self.plugin._unload(), 60)
        finally:
            self.plugin = None
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(5)
            shutil.rmtree(self.tmp, ignore_errors=True)

    def call(self, name, *args, timeout=120):
        return self.run(getattr(self.plugin, name)(*args), timeout)

    @property
    def core(self):
        return self.plugin.core

    @property
    def engine(self):
        return self.plugin.core.engine

    def status(self):
        return self.engine.status()

    def until(self, pred, timeout=30.0, what="condition", poll=0.05):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if pred():
                return True
            time.sleep(poll)
        raise TimeoutError(f"timed out waiting for {what}")

    def try_until(self, pred, timeout=30.0, poll=0.05):
        try:
            return self.until(pred, timeout, "", poll)
        except TimeoutError:
            return False

    # -- stimuli ----------------------------------------------------------------------------------------------
    def start_stimulus(self, screen, freeze=False):
        """Every 2.5-4 s the screen's stimulus path gets a new value from its source. freeze=True also pins every
        other key the screen shows to its current value, so the panel is idle between stimuli (engine latency alone)."""
        self.stop_stimulus()
        if screen not in STIM:
            return
        path, gen, push = STIM[screen]
        if freeze:
            snap = self.engine.registry.snapshot()
            scr = self.engine.screens[screen]
            bound = scr.bound_paths() if hasattr(scr, "bound_paths") else list(snap)   # the old engine has no bound_paths
            with self.lock:
                self.frozen = {k: snap[k] for k in bound if k != path and k in snap and not k.endswith(".history")
                               and not k.startswith("time.")}
                self.frozen_hist = {k: snap[k] for k in bound if k in snap and k.endswith(".history")}
        self._stim_stop = threading.Event()
        ev = self._stim_stop
        self.stim_path = path

        def loop():
            while not ev.wait(random.uniform(2.5, 4.0)):
                with self.lock:
                    v = gen()
                    while v in self.stim_by_val:
                        v = gen()
                    self.stim_id += 1
                    self.stim_by_val[v] = self.stim_id
                    self.overlay = {path: v}
                    self.stims.append({"id": self.stim_id, "t": time.monotonic(), "screen": screen, "value": v})
                if push:
                    self.push(push)

        self._stim_thread = threading.Thread(target=loop, name="hw-e2e-stim", daemon=True)
        self._stim_thread.start()

    def stop_stimulus(self):
        self._stim_stop.set()
        if self._stim_thread:
            self._stim_thread.join(5)
        with self.lock:
            self.overlay, self.frozen, self.frozen_hist = {}, {}, {}
        self.stim_path = None
        self._stim_thread = None

    # -- analysis ---------------------------------------------------------------------------------------------
    def window(self, t_a, t_b):
        """Completed batches whose first RECT/REFRESH was written in [t_a, t_b)."""
        with self.lock:
            return [dict(b) for b in self.batches if t_a <= (b.get("t_first") or b["t_refresh"]) < t_b and b["t_ack"]]

    def stim_latencies(self, t_a, t_b, screen):
        return [ms for ms, _ in self.stim_samples(t_a, t_b, screen)]

    def stim_samples(self, t_a, t_b, screen):
        """(ms, batch was a full refresh) from each stimulus to the ACK of the first batch rendered from a snapshot that included it."""
        with self.lock:
            batches = [dict(b) for b in self.batches if b["t_ack"] and t_a <= b["t_first"] < t_b + 10]
            stims = [s for s in self.stims if s["screen"] == screen and t_a <= s["t"] < t_b]
        out = []
        for s in stims:
            nxt = [b for b in batches if (b["stim"] or 0) >= s["id"] and b["t_first"] >= s["t"] - 1.0]
            if nxt:
                out.append(((nxt[0]["t_ack"] - s["t"]) * 1000, bool(nxt[0]["full"])))
        return out

    def stim_profile(self, t_a, t_b, screen):
        """Per stimulus: where the change-to-ACK time went (ms). notify = source sample delay; wait = until the engine took the
        snapshot that holds the value (a refresh in flight, tick, min gap); render = snapshot to first RECT (render + diff);
        send = first RECT to REFRESH written; panel = REFRESH written to ACK (USB + e-paper)."""
        with self.lock:
            batches = [dict(b) for b in self.batches if b["t_ack"] and t_a - 5 <= b["t_first"] < t_b + 10]
            stims = [s for s in self.stims if s["screen"] == screen and t_a <= s["t"] < t_b]
            notifies = list(self.notifies)
        out = []
        for s in stims:
            nxt = [b for b in batches if (b["stim"] or 0) >= s["id"] and b["t_first"] >= s["t"] - 1.0]
            if not nxt or not nxt[0]["t_snap"]:
                continue
            b = nxt[0]
            n = next((t for t in notifies if t >= s["t"]), None)
            busy = any(o["t_first"] <= s["t"] < o["t_ack"] for o in batches if o is not b and o["t_first"] < b["t_first"])
            out.append({"total": (b["t_ack"] - s["t"]) * 1000, "notify": (n - s["t"]) * 1000 if n is not None and n <= b["t_snap"] else None,
                        "wait": (b["t_snap"] - s["t"]) * 1000, "render": (b["t_first"] - b["t_snap"]) * 1000,
                        "send": (b["t_refresh"] - b["t_first"]) * 1000, "panel": (b["t_ack"] - b["t_refresh"]) * 1000,
                        "rects": b["rects"], "bytes": b["bytes"], "full": bool(b["full"]), "arrived_busy": busy})
        return out

    def save_fb(self, name):
        """PNG of the panel's shadow buffer (what we told it to show), next to the JSON results."""
        from PIL import Image
        with self.lock:
            raw = bytes(b ^ 0xFF for b in self.fb)
        d = self.out / "png"
        d.mkdir(exist_ok=True)
        Image.frombytes("1", (P.WIDTH, P.HEIGHT), raw).save(d / f"{name}.png")

    def dump(self, name, data):
        p = self.out / f"{name}.json"
        p.write_text(json.dumps(data, indent=2, default=str))
        print(f"  wrote {p}", flush=True)


# ---------------------------------------------------------------------------------------------------------------
# steps
# ---------------------------------------------------------------------------------------------------------------
def step_hello(a):
    """Raw HELLO over the real port, port open must not reset the chip, caps and version."""
    print("== hello", flush=True)
    path = panel_path()
    out = {"port": path}
    port = usb_link.open_port(path)
    link = usb_link.Link(port, log=lambda t: out.setdefault("log", []).append(t))
    h = None
    for _ in range(8):
        link.send(P.hello_request(link.next_seq()))
        for f in link.poll(2.0):
            if f.type == P.T_HELLO:
                h = P.parse_hello(f.payload)
        if h:
            break
    port.close()
    if not check(h is not None, "HELLO answered"):
        return out
    out["hello"] = h.__dict__
    check((h.proto_ver, h.width, h.height) == (1, 648, 480), "HELLO proto 1, 648x480", f"{h.proto_ver} {h.width}x{h.height}")
    check(bool(h.caps & P.CAP_IDLE_STORAGE), "caps: idle_storage", f"caps={h.caps}")
    check(bool(h.caps & P.CAP_OTA), "caps: ota", f"caps={h.caps}")
    check(bool(h.caps & P.CAP_GRAY4), "caps: gray4", f"caps={h.caps}")
    check(h.idle_flash_free == 49152, "idle store capacity 49152", str(h.idle_flash_free))
    print(f"  fw_ver {h.fw_ver!r}", flush=True)
    return out


def set_ghost_minutes(rig: Rig, minutes: int) -> dict:
    return rig.call("set_settings", {"ghost_clear_minutes": minutes})


def step_active(a, rig: Rig):
    print("== active", flush=True)
    out: dict = {"screens": {}}
    st = rig.status()
    check(st["connected"], "plugin connected to the real panel", f"fw {st['fw_version']!r}")
    # the 4 built-in active screens; big-clock shows seconds so every render differs
    r = rig.call("set_screen_settings", "big-clock", {"time_format": "%H:%M:%S"})
    check(r.get("ok"), "big-clock time_format with seconds", str(r))
    set_ghost_minutes(rig, 10)
    rig.call("pin_screen", "thermals-power")   # so that the first measured phase is a real screen switch too
    rig.until(lambda: rig.status()["current_screen"] == "thermals-power", 20, "start screen")
    time.sleep(6)
    for sid in ("telemetry", "thermals-power", "big-clock", "now-playing"):
        print(f"-- {sid}", flush=True)
        t_a = time.monotonic()   # before the switch: the screen-switch full refresh belongs to this phase
        if sid == "now-playing":
            rig.call("unpin_screen")   # a pin outranks now-playing
            r = rig.call("set_now_playing", APPID)
            check(r.get("ok"), "set_now_playing(real appid)", str(r))
        else:
            r = rig.call("pin_screen", sid)
            check(r.get("ok"), f"pin {sid}", str(r))
        rig.until(lambda: rig.status()["current_screen"] == sid, 20, f"screen {sid}")
        with rig.lock:
            n_before, naks_before = len(rig.batches), len(rig.naks)
        s0 = rig.status()
        rig.start_stimulus(sid)
        floor = a.min_s
        while True:
            time.sleep(1.0)
            el = time.monotonic() - t_a
            n_done = len(rig.window(t_a + 3, time.monotonic()))
            if (el >= floor and n_done >= a.updates) or el >= a.max_s:
                break
        rig.stop_stimulus()
        time.sleep(1.5)   # let the last batch finish
        t_b = time.monotonic()
        s1 = rig.status()
        bs = rig.window(t_a, t_b)
        # the first batch of the phase is the screen-switch full refresh; measure steady state after it
        kinds = ["F" if b["full"] else "P" for b in bs]
        # the first full refresh is the screen switch. A last batch of the previous screen can still land just before it (busy screens)
        i0 = kinds.index("F") if "F" in kinds else None
        steady = bs[i0 + 1:] if i0 is not None else []
        pipe = [(b["t_ack"] - b["t_snap"]) * 1000 for b in steady if b["t_snap"] and not b["full"]]
        pipe_full = [(b["t_ack"] - b["t_snap"]) * 1000 for b in steady if b["t_snap"] and b["full"]]
        e2e = rig.stim_latencies(t_a, t_b, sid)
        runs, run = [], 0
        for k in kinds[(i0 or 0) + 1:]:
            if k == "P":
                run += 1
            else:
                runs.append(run)
                run = 0
        d = {"duration_s": round(t_b - t_a, 1), "batches": len(bs), "partial": kinds.count("P"), "full": kinds.count("F"),
             "first_batch_full": i0 is not None and i0 <= 1, "partials_between_fulls": runs,
             "engine_partial_delta": s1["partial_refreshes"] - s0["partial_refreshes"],
             "engine_full_delta": s1["full_refreshes"] - s0["full_refreshes"],
             "pipeline_partial_ms": stats(pipe), "pipeline_full_ms": stats(pipe_full),
             "e2e_stimulus_ms": stats(e2e), "naks": len(rig.naks) - naks_before,
             "rects_per_batch": stats([b["rects"] for b in steady]), "bytes_per_batch": stats([b["bytes"] for b in steady]),
             "refresh_s": None, "errors": s1["last_error"]}
        out["screens"][sid] = d
        print(f"  {json.dumps({k: v for k, v in d.items() if k != 'partials_between_fulls'})}", flush=True)
        print(f"  partials between fulls: {runs}", flush=True)
        check(len(bs) >= a.updates, f"{sid}: >= {a.updates} refreshes measured", f"{len(bs)} in {d['duration_s']} s")
        check(d["first_batch_full"], f"{sid}: screen switch used a full refresh")
        check(d["naks"] == 0 and s1["naks"] == 0, f"{sid}: no NAKs")
        check(len(pipe) >= 25, f"{sid}: partial pipeline sample size", str(len(pipe)))
        if pipe:
            check(statistics.median(pipe) < 1000, f"{sid}: median render->refresh-complete < 1000 ms",
                  f"median {statistics.median(pipe):.0f} p90 {pctl(pipe, .9):.0f}")
        if sid in STIM:
            check(len(e2e) >= 25, f"{sid}: stimulus sample size", str(len(e2e)))
        # default ghost clear = 10 min since the last full refresh AND >= 30 partials: no run here lasts that long
        check(not runs, f"{sid}: no ghost-clear full refresh inside a {d['duration_s']:.0f} s run (default is every 10 min)", str(runs))
        if sid != "now-playing":
            pass
    # now-playing facts: appid, box art
    snap = rig.engine.registry.snapshot()
    np_ = snap.get("steam.now_playing") or {}
    art = np_.get("box_art_path")
    out["now_playing_snapshot"] = {k: (str(v) if k == "box_art_path" else v) for k, v in np_.items() if k != "last_achievement"}
    check(bool(art) and os.path.isfile(art), "now-playing: box art found on disk for the real appid", str(art))
    r = rig.call("set_now_playing", None)
    check(r.get("ok"), "set_now_playing(None)")
    rig.call("unpin_screen")

    print("-- rotation / pin / now-playing auto switch", flush=True)
    rot = ["telemetry", "thermals-power", "big-clock", "now-playing"]
    r = rig.call("set_rotation", rot, 10)
    check(r.get("ok"), "set_rotation 4 screens, 10 s", str(r))
    rig.call("unpin_screen")
    seen, t_a = [], time.monotonic()
    with rig.lock:
        nb = len(rig.batches)
    while time.monotonic() - t_a < 52:
        cur = rig.status()["current_screen"]
        if not seen or seen[-1][1] != cur:
            seen.append((time.monotonic() - t_a, cur))
        time.sleep(0.1)
    seq = [s for _, s in seen]
    print(f"  rotation: {[(round(t, 1), s) for t, s in seen]}", flush=True)
    i = rot.index(seq[0]) if seq[0] in rot else -1
    expect = [rot[(i + k) % 4] for k in range(len(seq))]
    check(len(seq) >= 5 and seq == expect, "rotation visits the screens in order every 10 s", str(seq))
    gaps = [seen[k + 1][0] - seen[k][0] for k in range(len(seen) - 1)]
    check(all(9 <= g <= 13 for g in gaps[1:]), "rotation interval ~10 s", str([round(g, 1) for g in gaps]))
    time.sleep(5.5)   # the last switch is a 4 s full refresh
    with rig.lock:
        new = [dict(b) for b in rig.batches[nb:] if b["t_ack"]]
    switch_fulls = 0
    for t, s in seen[1:]:
        tt = t_a + t
        near = [b for b in new if tt - 0.3 <= b["t_first"] <= tt + 3.0]
        if any(b["full"] for b in near):
            switch_fulls += 1
    check(switch_fulls == len(seen) - 1, "every rotation switch is a full refresh", f"{switch_fulls}/{len(seen) - 1}")
    out["rotation"] = [(round(t, 1), s) for t, s in seen]
    # pin overrides rotation
    r = rig.call("pin_screen", "big-clock")
    rig.until(lambda: rig.status()["current_screen"] == "big-clock", 15, "pin takes effect")
    time.sleep(25)   # > two rotation intervals
    check(rig.status()["current_screen"] == "big-clock" and rig.status()["pinned"] == "big-clock", "pin holds for 25 s across rotation ticks")
    # now-playing beats rotation but not pin
    rig.call("set_now_playing", APPID)
    time.sleep(2)
    check(rig.status()["current_screen"] == "big-clock", "pin beats now-playing")
    rig.call("unpin_screen")
    ok = rig.try_until(lambda: rig.status()["current_screen"] == "now-playing", 15)
    check(ok, "now-playing auto-switch after unpin (real appid)", rig.status()["current_screen"])
    time.sleep(8)
    rig.call("set_now_playing", None)
    ok = rig.try_until(lambda: rig.status()["current_screen"] != "now-playing", 15)
    check(ok, "set_now_playing(None) returns to rotation", rig.status()["current_screen"])
    st = rig.status()
    out["final_status"] = {k: st[k] for k in ("partial_refreshes", "full_refreshes", "naks", "reconnects", "last_error", "rects_sent", "bytes_sent", "heartbeats_sent")}
    check(st["naks"] == 0 and st["reconnects"] == 0, "no NAKs, no reconnects in the whole active run", str(out["final_status"]))
    panel_ref = [t for _, _, t in rig.plines if t.startswith("host partial")]
    out["panel_host_partial_lines"] = len(panel_ref)
    ms = [int(m.group(1)) for t in panel_ref if (m := re.match(r"host partial refresh (\d+) ms", t))]
    out["panel_reported_partial_ms"] = stats(ms)
    return out


def _draws(plines, t0=0.0):
    """Idle screen draws the panel logged: (panel_ms, kind, refresh_ms)."""
    out = []
    for t, ms, txt in plines:
        m = re.match(r"idle (full|partial|gray4) refresh (\d+) ms", txt)
        if m and ms is not None and t >= t0:
            out.append((ms, m.group(1), int(m.group(2))))
    return out


def step_idle(a, rig: Rig):
    print("== idle", flush=True)
    out: dict = {"passes": {}}
    eng = rig.engine

    def configure(ids, modes, rotate):
        r = rig.call("set_settings", {"idle_screens": ids, "idle_modes": modes, "idle_rotate_s": rotate, "idle_timeout_s": 20})
        check(r.get("ok"), f"configure idle {ids} modes={modes} rotate={rotate}", str(r.get("error", "")))
        t0 = time.monotonic()
        rig.until(lambda: rig.status()["idle"]["state"] == "ok" and rig.status()["idle"]["ids"] == ids, 90, "idle upload")
        up = time.monotonic() - t0
        time.sleep(3)   # MODE with the new timeout goes out on the next tick
        st = rig.status()["idle"]
        return {"upload_s": round(up, 1), "bytes": st["bytes"], "modes": st["modes"], "free": st["free"]}

    def idle_pass(name, observe_s):
        """Stop the host WITHOUT MODE PANEL (pause = port closed, nothing sent), wait, then wake the panel with
        engine.resume(). The panel queues its log while no session exists and delivers it, stamped with its own
        clock, when the session restarts."""
        t_ready = time.monotonic()
        # a live (not queued-and-replayed) host log line gives the panel-clock -> host-clock offset
        rig.until(lambda: any(t > t_ready and txt.startswith("host ") for t, _, txt in rig.plines), 40, "a live host log line")
        with rig.lock:
            ref = [(t, ms) for t, ms, txt in rig.plines if ms is not None and txt.startswith("host ") and t > t_ready]
        offset = (ref[-1][0] - ref[-1][1] / 1000.0) if ref else None   # host time - panel time, from the last live log line
        n0 = len(rig.plines)
        eng.pause()
        t_last = rig.last_tx   # the last frame the panel ever got from this host session
        time.sleep(observe_s)
        t_r = time.monotonic()
        with rig.lock:
            nb = len(rig.batches)
        eng.resume()
        rig.until(lambda: rig.status()["connected"], 60, "reconnect")
        t_conn = time.monotonic() - t_r
        rig.until(lambda: any(txt.startswith("idle: leave") for _, _, txt in rig.plines[n0:]), 10, "idle: leave line")
        lines = rig.plines[n0:]
        enter = [ms for _, ms, txt in lines if txt.startswith("idle: enter")][-1:]   # the last one: older queued text may precede it
        d = {"log": [(ms, txt) for _, ms, txt in lines if ms is not None][:40]}
        draws = [x for x in _draws(lines) if enter and x[0] >= enter[0]]
        d["draws"] = draws
        ok = bool(enter) and offset is not None
        check(bool(enter), f"{name}: panel entered idle by itself (log 'idle: enter')")
        if ok:
            delay = enter[0] / 1000.0 + offset - t_last
            d["enter_after_last_frame_s"] = round(delay, 1)
            check(19 <= delay <= 24, f"{name}: idle entered ~20 s after the last host frame", f"{delay:.1f} s")
        def first_full():
            with rig.lock:
                return [b for b in rig.batches[nb:] if b["t_ack"] and b["full"]]
        ok = rig.try_until(lambda: bool(first_full()), 30)
        check(ok, f"{name}: back in host mode with a full refresh")
        res = {"reconnect_s": round(t_conn, 1)}
        if ok:
            b = first_full()[0]
            res["resume_to_full_refresh_done_s"] = round(b["t_ack"] - t_r, 1)
            res["full_refresh_ms"] = round((b["t_ack"] - b["t_first"]) * 1000)
        left = [txt for _, _, txt in lines if txt.startswith("idle: leave")]
        check(bool(left), f"{name}: panel logged 'idle: leave'", left[0] if left else "")
        check(rig.status()["naks"] == 0, f"{name}: no NAKs after returning")
        d["back"] = res
        return d, draws

    rig.until(lambda: rig.status()["connected"], 30, "connected")
    # A: the three mono built-ins, rotating every 10 s
    ids = ["clock-weather", "steam-stats", "minimal-clock"]
    up = configure(ids, {}, 10)
    check(up["bytes"] > 0, "A: 3 mono layouts uploaded to the panel", str(up))
    d, draws = idle_pass("A (3 mono, rotate 10 s)", 62)
    out["passes"]["A_mono_rotation"] = {"upload": up, **d}
    kinds = [(round((ms - draws[0][0]) / 1000, 1), k, r) for ms, k, r in draws] if draws else []
    print(f"  A draws: {kinds}", flush=True)
    check(len(draws) >= 4, "A: >= 4 idle draws in ~40 s (3 layouts, rotating)", str(kinds))
    gaps = [(draws[i + 1][0] - draws[i][0]) / 1000 for i in range(len(draws) - 1)]
    check(bool(gaps) and all(8 <= g <= 14 for g in gaps[:5]), "A: layouts rotate every ~10 s", str([round(g, 1) for g in gaps[:6]]))
    # B: one mono layout, minute-tick partial refresh
    up = configure(["clock-weather"], {}, 0)
    d, draws = idle_pass("B (clock-weather mono)", 20 + 250)
    part = [r for _, k, r in draws if k == "partial"]
    d["partial_ms"] = stats(part)
    out["passes"]["B_mono_partial"] = {"upload": up, **d}
    print(f"  B draws: {[(k, r) for _, k, r in draws]}", flush=True)
    check(len(part) >= 3, "B: >= 3 idle partial refreshes measured", f"{part}")
    if part:
        check(statistics.median(part) < 1000, "B: idle mono partial refresh < 1000 ms", f"median {statistics.median(part):.0f} ms, max {max(part)}")
    # C: gray4 variant
    up = configure(["clock-weather-gray4"], {}, 0)
    d, draws = idle_pass("C (clock-weather-gray4)", 45)
    g = [r for _, k, r in draws if k == "gray4"]
    d["gray4_ms"] = stats(g)
    out["passes"]["C_gray4"] = {"upload": up, **d}
    check(bool(g), "C: gray4 layout drawn on the panel", f"{g} ms")
    # restore the default timeout the plugin would use
    rig.call("set_settings", {"idle_timeout_s": 60})
    time.sleep(3)
    check(rig.status()["naks"] == 0, "no NAKs in the whole idle run")
    return out


def step_packages(a, rig: Rig):
    print("== packages", flush=True)
    sys.path.insert(0, str(DECKY / "examples"))
    import make_package as MP
    out: dict = {}
    zdir = rig.tmp / "zips"
    zdir.mkdir()
    for name in ("hello-screen", "hello-code-screen"):
        print(f"-- {name}", flush=True)
        zp = zdir / f"{name}.zip"
        rc = MP.main([str(DECKY / "examples" / name), "-o", str(zp)])
        check(rc == 0 and zp.is_file(), f"{name}: make_package.py built a zip", f"{zp.stat().st_size if zp.is_file() else 0} bytes")
        r = rig.call("import_package", str(zp))
        check(r.get("ok"), f"{name}: imported through PluginCore", str(r.get("error", "")))
        pkg = r.get("package", {})
        code = name == "hello-code-screen"
        check(bool(r.get("needs_approval")) == code, f"{name}: needs_approval == {code}")
        if code:
            p = rig.call("pin_screen", name)
            check(not p.get("ok"), f"{name}: cannot be pinned before approval", str(p.get("error", "")))
            ap = rig.call("approve_package", name, pkg["version"], pkg["code_sha256"])
            check(ap.get("ok"), f"{name}: approval step", str(ap.get("error", "")))
            bad = rig.call("approve_package", name, pkg["version"], "0" * 64)
            check(not bad.get("ok"), f"{name}: approval with a wrong hash is refused")
        t_a = time.monotonic()
        p = rig.call("pin_screen", name)
        check(p.get("ok"), f"{name}: pinned", str(p.get("error", "")))
        rig.until(lambda: rig.status()["current_screen"] == name, 20, "screen shown")
        ok = rig.try_until(lambda: len(rig.window(t_a, time.monotonic())) >= 3, 40)
        bs = rig.window(t_a, time.monotonic())
        st = rig.status()
        check(ok and bs[0]["full"], f"{name}: first frame was a full refresh, then partial updates acked by the panel",
              f"{[('F' if b['full'] else 'P') for b in bs]}")
        check(st["naks"] == 0 and not st["last_error"], f"{name}: no NAKs, no engine error", str(st["last_error"]))
        black = sum(bin(b).count("1") for b in rig.fb)
        check(black > 2000, f"{name}: framebuffer sent to the panel is not blank", f"{black} black pixels")
        rig.save_fb(name)
        out[name] = {"batches": len(bs), "kinds": ["F" if b["full"] else "P" for b in bs], "black_pixels": black,
                     "pipeline_ms": stats([(b["t_ack"] - b["t_snap"]) * 1000 for b in bs[1:] if b["t_snap"]])}
    for name in ("hello-code-screen", "hello-screen"):
        r = rig.call("uninstall_package", name)
        check(r.get("ok"), f"{name}: uninstalled")
    ok = rig.try_until(lambda: rig.status()["current_screen"] not in ("hello-screen", "hello-code-screen"), 15)
    check(ok, "engine falls back to a built-in screen after uninstall", rig.status()["current_screen"])
    return out


class FakeGitHub:
    """A local HTTP server that answers the two GitHub URLs the updater uses. `fetch` maps the real https://
    api.github.com / https://github.com URLs onto it, so the plugin's URL allow-list and parsing run unchanged."""

    def __init__(self):
        import http.server
        self.dirs: dict[str, Path] = {}   # tag -> directory holding manifest.json, manifest.sig, the .bin
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.path.startswith("/api/repos/FliesWithWind/inkterface/releases"):
                    body, code = json.dumps(outer.listing()).encode(), 200
                elif self.path.startswith("/dl/"):
                    try:
                        _, _, tag, name = self.path.split("/", 3)
                        body, code = (outer.dirs[tag] / name).read_bytes(), 200
                    except (KeyError, ValueError, OSError):
                        body, code = b"not found", 404
                else:
                    body, code = b"not found", 404
                self.send_response(code)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)   # loopback only
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, name="hw-e2e-fakegh", daemon=True).start()

    def listing(self):
        out = []
        for tag, d in self.dirs.items():
            assets = [{"name": f.name, "size": f.stat().st_size,
                       "browser_download_url": f"https://github.com/FliesWithWind/inkterface/releases/download/{tag}/{f.name}"}
                      for f in sorted(d.iterdir())]
            out.append({"tag_name": tag, "draft": False, "prerelease": False, "assets": assets,
                        "html_url": f"https://github.com/FliesWithWind/inkterface/releases/tag/{tag}"})
        return out

    def fetch(self, url, max_bytes, timeout=20.0):
        import urllib.error
        import urllib.request
        from inkterface_core.ota import github
        if url.startswith("https://api.github.com/"):
            local = f"http://127.0.0.1:{self.port}/api/" + url[len("https://api.github.com/"):]
        elif url.startswith("https://github.com/FliesWithWind/inkterface/releases/download/"):
            local = f"http://127.0.0.1:{self.port}/dl/" + url[len("https://github.com/FliesWithWind/inkterface/releases/download/"):]
        else:
            raise github.NetworkError(f"fake GitHub does not serve {url}")
        try:
            r = urllib.request.urlopen(local, timeout=timeout)
        except urllib.error.HTTPError as e:
            r = e
        with r:
            body = r.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise github.TooLarge(f"response exceeds {max_bytes} bytes")
            return github.Response(r.status, body, {k.lower(): v for k, v in r.headers.items()})

    def close(self):
        self.srv.shutdown()


def build_fw(version: str | None, dest: Path) -> Path:
    """pio build of the ee05 env with INKTERFACE_FW_VERSION=<version> (None = dev build); copies firmware.bin."""
    import subprocess
    pre = os.environ.get("INKTERFACE_PREBUILT_FW")   # dir with <version>.bin built elsewhere (the Steam Machine has no pio)
    if pre and version:
        shutil.copyfile(Path(pre) / f"{version}.bin", dest)
        return dest
    env = {k: v for k, v in os.environ.items() if k != "INKTERFACE_FW_VERSION"}
    if version:
        env["INKTERFACE_FW_VERSION"] = version
    r = subprocess.run(["pio", "run", "-e", "ee05"], cwd=REPO / "firmware", env=env, capture_output=True, text=True, timeout=900)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    shutil.copyfile(REPO / "firmware" / ".pio" / "build" / "ee05" / "firmware.bin", dest)
    return dest


def step_ota(a):
    print("== ota", flush=True)
    import errno
    sys.path.insert(0, str(REPO / "tools" / "release"))
    import sign_manifest
    from inkterface_core.ota import ed25519
    work = Path(tempfile.mkdtemp(prefix="hw-e2e-ota-"))
    out: dict = {}
    seed, wrong = os.urandom(32), os.urandom(32)   # TEST-ONLY keys, new every run, never written outside `work`
    pub = ed25519.publickey(seed)
    gh = FakeGitHub()
    try:
        fw_a, fw_b = work / "a.bin", work / "b.bin"
        print("-- building images (1.0.1 good; 1.0.3 labelled 1.0.2 in its manifest so the host must refuse to ack it)", flush=True)
        if not a.rollback_only:
            build_fw("1.0.1", fw_a)
        build_fw("1.0.3", fw_b)
        out["image_bytes"] = {"bad": fw_b.stat().st_size, **({} if a.rollback_only else {"good": fw_a.stat().st_size})}
        kw = dict(min_plugin_version="0.0.1", changelog="- hardware test", protocol_version=P.PROTO_VER)
        if not a.rollback_only:
            sign_manifest.build(str(fw_a), "9.9.9", wrong, str(work / "r_wrong"), **kw)      # signed with the wrong key
            sign_manifest.build(str(fw_a), "1.0.1", seed, str(work / "r_a"), **kw)
        sign_manifest.build(str(fw_b), "1.0.2", seed, str(work / "r_b"), **kw)
        rig = Rig(Path(a.out), firmware_kw=dict(fw_fetch=gh.fetch, public_key=lambda: pub, confirm_timeout=60.0,
                                                handshake_timeout=60.0, tick_s=3600.0), wire_log=a.verbose)
        try:
            rig.start()
            fw = lambda: rig.core.firmware   # noqa: E731
            st = rig.status()
            out["fw_before"] = st["fw_version"]
            print(f"  panel runs {st['fw_version']!r}", flush=True)
            if not a.rollback_only:
                # 0. a release signed with another key is refused
                gh.dirs["fw-v9.9.9"] = work / "r_wrong"
                s = rig.call("check_firmware")
                check(s["check"]["ok"] is False and s["latest"] is None, "release signed with the wrong key is refused", str(s["check"]["error"]))
                del gh.dirs["fw-v9.9.9"]
                # 1. good release
                gh.dirs["fw-v1.0.1"] = work / "r_a"
                s = rig.call("check_firmware")
                check(s["check"]["ok"] and s["update_available"] and s["latest"]["version"] == "1.0.1", "check finds the signed 1.0.1 release",
                      str(s["check"]))
                t0 = time.monotonic()
                r = rig.call("install_firmware", "1.0.1")
                check(r.get("ok"), "install_firmware accepted", str(r))
                timeline, injected, last = [], False, None
                while True:
                    s = rig.call("get_firmware_status")["install"]
                    key = (s["state"], s["percent"] // 10)
                    if key != last:
                        timeline.append((round(time.monotonic() - t0, 1), s["state"], s["percent"]))
                        last = key
                    if s["state"] == "sending" and s["percent"] >= 40 and not injected:
                        injected = True
                        rig.ports[-1]._err = OSError(errno.EIO, "injected unplug")   # cable pulled mid-transfer
                        timeline.append((round(time.monotonic() - t0, 1), "INJECTED unplug", s["percent"]))
                    if s["state"] in ("done", "failed", "cancelled"):
                        break
                    if time.monotonic() - t0 > 400:
                        break
                    time.sleep(0.25)
                t_done = time.monotonic()
                out["install_good"] = {"timeline": timeline, "total_s": round(t_done - t0, 1), "final": s,
                                       "begin_acks_received": list(rig.begin_acks)}
                print(f"  timeline {timeline}", flush=True)
                check(s["state"] == "done", "install 1.0.1: stream, reboot, HELLO, confirm_boot", f"{s['state']} {s['error']} in {t_done - t0:.0f} s")
                check(injected and len(rig.begin_acks) >= 2 and rig.begin_acks[-1] > 0, "cable pulled at 40%: stream resumed from the panel's offset",
                      f"OTA_BEGIN acks (bytes already held): {rig.begin_acks}")
                ok = rig.try_until(lambda: rig.status()["connected"] and rig.status()["fw_version"] == "1.0.1", 90)
                check(ok, "engine is back and the panel reports fw 1.0.1", str(rig.status()["fw_version"]))
                fs = rig.call("get_firmware_status")
                check(fs["installed"]["version"] == "1.0.1" and not fs["update_available"], "plugin shows 1.0.1 installed, no update pending")
                # still 1.0.1 after the 180 s verify window = the host's ack really marked the image valid
                print("-- waiting 195 s: the image must survive its 180 s verify window", flush=True)
                time.sleep(195)
                st = rig.status()
                check(st["connected"] and st["fw_version"] == "1.0.1", "1.0.1 survived the verify window (ack accepted, no rollback)", str(st["fw_version"]))
            # 2. rollback: the image boots, but the host cannot ack it
            gh.dirs["fw-v1.0.2"] = work / "r_b"
            s = rig.call("check_firmware")
            check(s["update_available"] and s["latest"]["version"] == "1.0.2", "check finds release 1.0.2", str(s["check"]))
            fwlog, stop = [], threading.Event()

            def watch():
                last = None
                while not stop.is_set():
                    v = rig.status()
                    cur = (v["connected"], v["fw_version"])
                    if cur != last:
                        fwlog.append((time.monotonic(), *cur))
                        last = cur
                    time.sleep(0.2)

            th = threading.Thread(target=watch, daemon=True)
            th.start()
            t1 = time.monotonic()
            r = rig.call("install_firmware", "1.0.2")
            check(r.get("ok"), "install_firmware 1.0.2 accepted", str(r))
            t_conf = None
            while time.monotonic() - t1 < 400:
                s = rig.call("get_firmware_status")["install"]
                if s["state"] == "confirming" and t_conf is None:
                    t_conf = time.monotonic()
                if s["state"] in ("done", "failed", "cancelled"):
                    break
                time.sleep(0.5)
            check(s["state"] == "failed" and s["rollback_pending"], "host refuses to ack the image (reports 1.0.3, manifest says 1.0.2)",
                  f"{s['state']}: {s['error']}")
            # the panel must roll back by itself, ~180 s after it booted the new image
            ok = rig.try_until(lambda: any(v == "1.0.3" for _, _, v in fwlog) and rig.status()["connected"] and rig.status()["fw_version"] == "1.0.1", 300)
            time.sleep(1.5)   # let the watcher log the transition that satisfied the condition
            stop.set()
            th.join(2)
            seen_new = [t for t, c, v in fwlog if v == "1.0.3"]
            back = [t for t, c, v in fwlog if v == "1.0.1" and seen_new and t > seen_new[0]]
            d = {"fw_log": [(round(t - t1, 1), c, v) for t, c, v in fwlog],
                 "new_image_first_seen_s": round(seen_new[0] - t1, 1) if seen_new else None,
                 "rolled_back_seen_s": round(back[0] - t1, 1) if back else None,
                 "install_end_to_rollback_s": round(back[0] - t_conf, 1) if back and t_conf else None}
            out["rollback"] = d
            print(f"  {d}", flush=True)
            check(ok and bool(seen_new), "panel booted the unacked image (fw 1.0.3 seen)")
            check(ok and bool(back), "bootloader rolled back to 1.0.1 by itself", str(d["install_end_to_rollback_s"]))
            if back and t_conf:
                check(150 <= back[0] - t_conf <= 260, "rollback within the verify window (180 s + reboot)", f"{back[0] - t_conf:.0f} s after the image was sent")
            st = rig.status()
            check(st["connected"] and st["fw_version"] == "1.0.1", "finished on the good version", str(st["fw_version"]))
            out["fw_after"] = st["fw_version"]
        finally:
            rig.stop()
    finally:
        gh.close()
        shutil.rmtree(work, ignore_errors=True)
    return out


def _tty_usb_node() -> tuple[str, str]:
    tty = os.path.basename(os.path.realpath(panel_path()))
    d = os.path.realpath(f"/sys/class/tty/{tty}/device")
    while d != "/" and not os.path.exists(os.path.join(d, "idVendor")):
        d = os.path.dirname(d)
    bus, dev = int(Path(d, "busnum").read_text()), int(Path(d, "devnum").read_text())
    return f"/dev/bus/usb/{bus:03d}/{dev:03d}", d


def step_robust(a, rig: Rig):
    print("== robust", flush=True)
    import errno
    import fcntl
    import subprocess
    out: dict = {}
    eng = rig.engine

    def clock_offset():
        """host time - panel time, from a live log line. Equal offsets before/after = the panel did not reboot."""
        t_mark = time.monotonic()
        rig.until(lambda: any(t > t_mark and ms is not None and txt.startswith("host ") for t, ms, txt in rig.plines), 30, "live log line")
        t, ms, _ = [x for x in rig.plines if x[0] > t_mark and x[1] is not None and x[2].startswith("host ")][0]
        return t - ms / 1000.0

    base = clock_offset()
    out["clock_offsets"] = {"start": 0.0}

    def same_chip(label):
        off = clock_offset() - base
        out["clock_offsets"][label] = round(off, 2)
        check(abs(off) < 0.5, f"panel clock continuous after {label} (no reboot)", f"drift {off:+.2f} s")

    # 1. unplug-equivalent: the port starts failing with EIO, as it does when the cable is pulled
    rc0, nb = rig.status()["reconnects"], len(rig.batches)
    t = time.monotonic()
    rig.ports[-1]._err = OSError(errno.EIO, "injected unplug")
    ok = rig.try_until(lambda: rig.status()["reconnects"] > rc0 and rig.status()["connected"], 60)
    dt = time.monotonic() - t
    check(ok, "port failure -> engine reconnects by itself", f"{dt:.1f} s")
    ok = rig.try_until(lambda: any(b["t_ack"] and b["full"] for b in rig.batches[nb:]), 30)
    check(ok, "full redraw after the reconnect")
    skipped = rig.ports[-1]._rx.skipped
    check(skipped < 64, "re-opening did not reset the chip (no boot ROM text on the new port)", f"{skipped} non-frame bytes")
    out["unplug_equivalent"] = {"reconnect_s": round(dt, 1), "noise_bytes": skipped}
    same_chip("port failure + reopen")
    # 2. opening the port does not reset the chip
    eng.pause()
    time.sleep(1.0)
    rows = []
    for i in range(6):
        t0 = time.monotonic()
        port = usb_link.open_port(panel_path())
        bits = struct.unpack("I", fcntl.ioctl(port._fd, 0x5415, struct.pack("I", 0)))[0]   # TIOCMGET
        link = usb_link.Link(port)
        link.send(P.hello_request(link.next_seq()))
        got, t_h = False, None
        end = time.monotonic() + 3
        while time.monotonic() < end and not got:
            for f in link.poll(0.05):
                got = got or f.type == P.T_HELLO
        t_h = time.monotonic() - t0
        rows.append({"dtr": bool(bits & 2), "rts": bool(bits & 4), "hello_s": round(t_h, 3), "noise": link.dec.skipped})
        port.close()
        time.sleep(0.3)
    out["raw_opens"] = rows
    check(all(not r["dtr"] and not r["rts"] for r in rows), "DTR and RTS are low after open (x6)")
    check(all(r["hello_s"] < 0.5 and r["noise"] < 64 for r in rows), "HELLO answered < 0.5 s after each open, no boot text (a reset takes seconds)",
          f"{[r['hello_s'] for r in rows]}")
    eng.resume()
    rig.until(lambda: rig.status()["connected"], 60, "reconnect")
    same_chip("6 raw opens")
    # 3. USB bus reset (what usbreset does): needs write access to the device node
    try:
        node, sysd = _tty_usb_node()
        out["usb_node"] = node
        fd = os.open(node, os.O_WRONLY)
    except OSError as e:
        fd = None
        print(f"  note: cannot open the USB node for reset: {e}", flush=True)
        check(False, "USBDEVFS_RESET possible without sudo", str(e))
    if fd is not None:
        rc0, nb = rig.status()["reconnects"], len(rig.batches)
        t = time.monotonic()
        try:
            fcntl.ioctl(fd, 0x5514)   # USBDEVFS_RESET
            err = None
        except OSError as e:
            err = e
        finally:
            os.close(fd)
        check(err is None, "USBDEVFS_RESET ioctl (usbreset equivalent) without sudo", str(err or "ok"))
        ok = rig.try_until(lambda: rig.status()["reconnects"] > rc0 and rig.status()["connected"], 90)
        dt = time.monotonic() - t
        check(ok, "after a USB bus reset the engine finds the panel again", f"{dt:.1f} s, now {panel_path()}")
        ok2 = rig.try_until(lambda: any(b["t_ack"] and b["full"] for b in rig.batches[nb:]), 30)
        check(ok2, "full redraw after the USB reset")
        out["usb_reset"] = {"reconnect_s": round(dt, 1), "noise_bytes": rig.ports[-1]._rx.skipped}
        same_chip("USB bus reset")
    # 4. edge_test against the real panel (it needs the port: hand it over)
    eng.pause()
    time.sleep(1.0)
    t = time.monotonic()
    r = subprocess.run([sys.executable, str(DECKY / "tools" / "edge_test.py"), "--slow"], capture_output=True, text=True, timeout=900)
    lines = (r.stdout + r.stderr).splitlines()
    fails = [l for l in lines if "[FAIL]" in l]
    oks = [l for l in lines if "[ OK ]" in l]
    out["edge_test"] = {"exit": r.returncode, "ok": len(oks), "fail": len(fails), "seconds": round(time.monotonic() - t, 1),
                        "tail": lines[-6:]}
    check(r.returncode == 0 and not fails and len(oks) >= 18, "edge_test.py --slow against the real panel", f"{len(oks)} OK, {len(fails)} FAIL, exit {r.returncode}")
    (rig.out / "edge_test.log").write_text(r.stdout + r.stderr)
    nb = len(rig.batches)
    eng.resume()
    rig.until(lambda: rig.status()["connected"], 60, "reconnect")
    ok = rig.try_until(lambda: any(b["t_ack"] and b["full"] for b in rig.batches[nb:]), 30)
    check(ok, "plugin takes the display back after edge_test (MODE HOST + full redraw)")
    check(rig.status()["naks"] == 0, "no NAKs through the robustness run", str(rig.status()["naks"]))
    return out


def _rss_kb():
    for l in open("/proc/self/status"):
        if l.startswith("VmRSS:"):
            return int(l.split()[1])


def step_ghost(a, rig: Rig):
    """Ghost-clear interval on Telemetry (never rests, so the clear is forced at the cap) and the latency while it runs."""
    print(f"== ghost: telemetry, {a.minutes} min, ghost_clear_minutes={a.ghost_min}", flush=True)
    out: dict = {"ghost_clear_minutes": a.ghost_min, "minutes": a.minutes}
    r = set_ghost_minutes(rig, a.ghost_min)
    out["setting_call"] = r.get("ok")
    rig.call("set_now_playing", None)
    t_pin = time.monotonic()
    rig.call("pin_screen", "telemetry")
    rig.until(lambda: rig.status()["current_screen"] == "telemetry", 20, "screen telemetry")
    time.sleep(6)   # screen-switch full refresh
    s0 = rig.status()
    t_a = time.monotonic()
    rig.start_stimulus("telemetry")
    end = t_a + a.minutes * 60
    nxt = t_a + 60
    while time.monotonic() < end:
        time.sleep(5)
        if time.monotonic() >= nxt:
            st = rig.status()
            print(f"  {(time.monotonic() - t_a) / 60:.0f} min: full {st['full_refreshes'] - s0['full_refreshes']} partial {st['partial_refreshes'] - s0['partial_refreshes']} "
                  f"deferred {st['ghost_deferred'] - s0['ghost_deferred']} naks {st['naks']}", flush=True)
            nxt += 60
    rig.stop_stimulus()
    time.sleep(6.0)
    t_b = time.monotonic()
    s1 = rig.status()
    bs = rig.window(0.0, t_b)   # since the rig started, so the connect / screen-switch full refresh is the first one
    acks = [b["t_ack"] for b in bs if b["full"]]
    inter = [round(y - x, 1) for x, y in zip(acks, acks[1:])]
    sm = rig.stim_samples(t_a, t_b, "telemetry")
    out.update({"duration_s": round(t_b - t_a, 1), "full_ack_times_s": [round(t - t_a, 1) for t in acks], "intervals_s": inter,
                "partial": sum(1 for b in bs if not b["full"]), "full": len(acks),
                "latency_all_ms": stats([ms for ms, _ in sm]), "latency_partial_ms": stats([ms for ms, f in sm if not f]),
                "full_samples": sum(1 for _, f in sm if f), "naks": s1["naks"], "reconnects": s1["reconnects"],
                "ghost_deferred": s1["ghost_deferred"] - s0["ghost_deferred"], "last_error": s1["last_error"]})
    print(f"  {json.dumps({k: v for k, v in out.items() if k != 'full_ack_times_s'})}", flush=True)
    m = a.ghost_min
    if m:
        lo, hi = m * 60 - 5, m * 60 * 1.5 + 15   # due at m minutes, forced at 1.5 m; the ACK of a 4 s refresh adds a little
        check(len(inter) >= 1 and any(t >= t_a for t in acks), "at least one ghost-clear full refresh happened", str(inter))
        check(all(lo <= i <= hi for i in inter), f"every full-refresh interval is {m * 60:.0f}..{m * 90:.0f} s (setting .. cap)", str(inter))
        n_in = sum(1 for t in acks if t >= t_a)   # ghost clears inside the measured window (the screen-switch full is before it)
        check(n_in >= int(out["duration_s"] // (m * 90 + 6)), "no ghost clear was skipped", f"{n_in} in {out['duration_s']:.0f} s")
    elif not m:
        check(not any(t >= t_a for t in acks), "0 = never: no full refresh after the screen switch", str(out["full_ack_times_s"]))
    check(out["naks"] == 0 and out["reconnects"] == 0, "no NAKs, no reconnects")
    check(len(sm) >= 25, "stimulus sample size", str(len(sm)))
    if sm:
        check(statistics.median([ms for ms, _ in sm]) < 1000, "median change-to-repaint < 1000 ms",
              f"median {statistics.median([ms for ms, _ in sm]):.0f} p90 {pctl([ms for ms, _ in sm], .9):.0f}")
    return out


def step_soak(a, rig: Rig):
    print(f"== soak {a.minutes} min", flush=True)
    warnings: list[str] = []

    class H(logging.Handler):
        def emit(self, rec):
            if rec.levelno >= logging.WARNING:
                warnings.append(f"{rec.levelname} {rec.name}: {rec.getMessage()}")

    h = H()
    logging.getLogger("inkterface_core").addHandler(h)
    out: dict = {"samples": []}
    r = set_ghost_minutes(rig, 10)
    rig.call("unpin_screen")
    rig.call("set_rotation", ["telemetry", "thermals-power", "big-clock", "now-playing"], 30)
    t0 = time.monotonic()
    end = t0 + a.minutes * 60
    next_np, np_on = t0 + 300, False
    st0 = rig.status()
    while time.monotonic() < end:
        time.sleep(30)
        now = time.monotonic()
        if now >= next_np:   # a game starts for a minute every 5 minutes
            np_on = not np_on
            rig.call("set_now_playing", APPID if np_on else None)
            next_np = now + (60 if np_on else 240)
        st = rig.status()
        heap = [(int(m.group(1)), int(m.group(2))) for _, t in rig.logs[-40:] if (m := re.search(r"heap (\d+) min (\d+)", t))]
        s = {"t_s": round(now - t0), "screen": st["current_screen"], "partial": st["partial_refreshes"], "full": st["full_refreshes"],
             "naks": st["naks"], "reconnects": st["reconnects"], "rss_kb": _rss_kb(), "threads": threading.active_count(),
             "fds": len(os.listdir("/proc/self/fd")), "heap_free": heap[-1][0] if heap else None, "heap_min": heap[-1][1] if heap else None,
             "err": st["last_error"]}
        out["samples"].append(s)
        print(f"  {s}", flush=True)
    rig.call("set_now_playing", None)
    st = rig.status()
    hv = [(t - t0, int(m.group(1)), int(m.group(2))) for t, txt in rig.logs if (m := re.search(r"heap (\d+) min (\d+)", txt))]
    span = lambda lo, hi: [f for t, f, _ in hv if lo <= t < hi]   # noqa: E731
    total = end - t0
    first, last = span(0, 300), span(total - 300, total + 60)
    rs = [x["rss_kb"] for x in out["samples"]]
    out.update({
        "duration_s": round(time.monotonic() - t0), "partial": st["partial_refreshes"] - st0["partial_refreshes"],
        "full": st["full_refreshes"] - st0["full_refreshes"], "naks": st["naks"], "reconnects": st["reconnects"],
        "last_error": st["last_error"], "warnings": warnings[:20], "n_warnings": len(warnings),
        "panel_heap_lines": len(hv),
        "panel_heap_free_first5min_median": statistics.median(first) if first else None,
        "panel_heap_free_last5min_median": statistics.median(last) if last else None,
        "panel_heap_free_min": min(f for _, f, _ in hv) if hv else None,
        "panel_heap_min_ever_first": hv[0][2] if hv else None, "panel_heap_min_ever_last": hv[-1][2] if hv else None,
        "host_rss_kb_first": rs[0] if rs else None, "host_rss_kb_last": rs[-1] if rs else None,
        "host_rss_kb_max": max(rs) if rs else None,
        "threads_first": out["samples"][0]["threads"] if rs else None, "threads_last": out["samples"][-1]["threads"] if rs else None,
        "fds_first": out["samples"][0]["fds"] if rs else None, "fds_last": out["samples"][-1]["fds"] if rs else None,
    })
    logging.getLogger("inkterface_core").removeHandler(h)
    check(st["naks"] == 0, "soak: no NAKs", str(st["naks"]))
    check(st["reconnects"] == 0, "soak: no reconnects", str(st["reconnects"]))
    check(not warnings, "soak: no warnings/errors logged by the plugin", str(warnings[:3]))
    check(st["last_error"] is None, "soak: engine last_error is empty", str(st["last_error"]))
    check(out["partial"] > 500, "soak: refreshes kept flowing", f"{out['partial']} partial + {out['full']} full")
    if first and last:
        check(statistics.median(last) >= statistics.median(first) - 4096, "soak: panel free heap did not shrink (last 5 min vs first 5 min)",
              f"{statistics.median(first):.0f} -> {statistics.median(last):.0f} bytes")
        check(hv[-1][2] >= hv[0][2] - 1024, "soak: panel lowest-ever heap did not drop", f"{hv[0][2]} -> {hv[-1][2]}")
    else:
        check(False, "soak: panel heap readings present")
    if rs:
        check(rs[-1] <= rs[min(len(rs) - 1, 10)] * 1.25 + 5000, "soak: host RSS did not grow > 25% after the first 5 min", f"{rs[min(len(rs) - 1, 10)]} -> {rs[-1]} kB")
    return out


def _cpu_by_thread():
    """{thread name prefix: cpu seconds} for this process (utime + stime from /proc/self/task)."""
    names = {t.native_id: t.name for t in threading.enumerate()}
    tick = os.sysconf("SC_CLK_TCK")
    out: dict = {}
    for tid in os.listdir("/proc/self/task"):
        try:
            raw = open(f"/proc/self/task/{tid}/stat").read()
        except OSError:
            continue
        f = raw[raw.rindex(")") + 2:].split()
        n = names.get(int(tid), "other")
        n = "src-*" if n.startswith("src-") else "hw-e2e (harness)" if n.startswith("hw-e2e") else "asyncio loop (harness)" if n.startswith("Thread-") else n
        out[n] = out.get(n, 0.0) + (int(f[11]) + int(f[12])) / tick
    return out


def step_cpu(a, rig: Rig):
    """Plugin CPU % (share of one core) on live data, per screen. Same step runs on the old and the new engine."""
    print(f"== cpu ({a.cpu_s} s per screen, live data, no stimulus)", flush=True)
    out: dict = {"seconds": a.cpu_s, "screens": {}}
    set_ghost_minutes(rig, 10)
    rig.call("set_now_playing", None)
    for sid in ("telemetry", "thermals-power", "big-clock"):
        rig.call("pin_screen", sid)
        rig.until(lambda: rig.status()["current_screen"] == sid, 20, f"screen {sid}")
        time.sleep(10)   # screen-switch full refresh and warm-up
        c0, s0, t0 = _cpu_by_thread(), rig.status(), time.monotonic()
        time.sleep(a.cpu_s)
        c1, s1, t1 = _cpu_by_thread(), rig.status(), time.monotonic()
        wall = t1 - t0
        by = {k: round((c1.get(k, 0) - c0.get(k, 0)) / wall * 100, 2) for k in c1}
        plugin = round(sum(v for k, v in by.items() if "harness" not in k), 2)
        d = {"cpu_pct_plugin": plugin, "cpu_pct_by_thread": by,
             "refreshes_per_s": round((s1["partial_refreshes"] + s1["full_refreshes"] - s0["partial_refreshes"] - s0["full_refreshes"]) / wall, 2),
             "renders_per_s": round((s1["frames_rendered"] - s0["frames_rendered"]) / wall, 2),
             "unchanged_per_s": round((s1["unchanged_ticks"] - s0["unchanged_ticks"]) / wall, 2)}
        out["screens"][sid] = d
        print(f"  {sid}: {json.dumps(d)}", flush=True)
    rig.call("unpin_screen")
    return out


def wire_vs_render(rig, sid) -> int:
    """No missed update: freeze what `sid` shows, let the engine converge, and count the bits where the frame we told the panel
    to show (rebuilt from the RECTs on the wire) differs from a fresh render of the same snapshot. 0 = the glass is up to date."""
    from inkterface_core.screens import render_frame
    scr, snap = rig.engine.screens[sid], rig.engine.registry.snapshot()
    keep = [k for k in scr.bound_paths() if k in snap]
    with rig.lock:
        rig.frozen = {k: snap[k] for k in keep if not k.endswith(".history")}
        rig.frozen_hist = {k: snap[k] for k in keep if k.endswith(".history")}
    time.sleep(3.5)
    snap = rig.engine.registry.snapshot()
    snap.update(rig.frozen_hist)
    want = bytes(b ^ 0xFF for b in render_frame(scr, snap, rig.engine.settings.screen_settings.get(sid)))
    with rig.lock:
        fb = bytes(rig.fb)
        rig.frozen, rig.frozen_hist = {}, {}
    return sum(bin(x ^ y).count("1") for x, y in zip(fb, want))


def step_latency(a, rig: Rig):
    """Change-to-complete latency per screen. live = real data keeps changing too; isolated = every other value frozen."""
    print("== latency", flush=True)
    out: dict = {"modes": {}}
    set_ghost_minutes(rig, 10)
    for mode in [m for m in a.modes.split(",") if m in ("live", "isolated")]:
        res = out["modes"][mode] = {}
        for sid in a.screens.split(","):
            print(f"-- {mode} {sid}", flush=True)
            rig.call("set_now_playing", None)
            if sid == "now-playing":
                rig.call("unpin_screen")
                rig.call("set_now_playing", APPID)
            else:
                rig.call("pin_screen", sid)
            rig.until(lambda: rig.status()["current_screen"] == sid, 20, f"screen {sid}")
            time.sleep(6)   # screen-switch full refresh (4 s)
            t_a = time.monotonic()
            rig.start_stimulus(sid, freeze=(mode == "isolated"))
            while len([x for x in rig.stims if x["screen"] == sid and x["t"] >= t_a]) < a.stims and time.monotonic() - t_a < 400:
                time.sleep(1)
            rig.stop_stimulus()
            time.sleep(6.0)   # the last stimulus's refresh may queue behind a 4 s ghost-clear: do not drop the slowest sample
            t_b = time.monotonic()
            bits = wire_vs_render(rig, sid) if mode == "live" else None
            sm = rig.stim_samples(t_a, t_b, sid)
            bs = rig.window(t_a, t_b)
            busy = sum(b["t_ack"] - (b.get("t_first") or b["t_refresh"]) for b in bs) / (t_b - t_a)
            d = {"all": stats([ms for ms, _ in sm]), "partial_only": stats([ms for ms, full in sm if not full]),
                 "ghost_clear_full_samples": sum(1 for _, full in sm if full), "panel_busy_fraction": round(busy, 2),
                 "batches": len(bs), "pipeline_partial_ms": stats([(b["t_ack"] - b["t_snap"]) * 1000 for b in bs if b["t_snap"] and not b["full"]])}
            prof = rig.stim_profile(t_a, t_b, sid)
            pp = [x for x in prof if not x["full"]]
            d["profile_partial_median_ms"] = {k: round(statistics.median([x[k] for x in pp if x[k] is not None]), 1)
                                              for k in ("total", "notify", "wait", "render", "send", "panel") if any(x[k] is not None for x in pp)}
            with rig.lock:
                pl = [m.groups() for tl, _ms, txt in rig.plines if t_a <= tl < t_b
                      for m in [re.match(r"host partial refresh (\d+) ms \(refresh (\d+), power (\d+)\+(\d+), data (\d+)\)", txt)] if m]
            if pl:   # what the firmware itself timed per partial refresh: total, e-paper waveform, DC-DC on + off, SPI data
                d["panel_log_partial_median_ms"] = dict(zip(("total", "refresh", "power_on", "power_off", "data"),
                                                            (statistics.median(int(x[i]) for x in pl) for i in range(5))))
            d["arrived_while_panel_busy"] = f"{sum(1 for x in pp if x['arrived_busy'])}/{len(pp)}"
            d["samples_ms"] = [round(ms) for ms, full in sm if not full]
            d["median_of_10_blocks_ms"] = [round(statistics.median(d["samples_ms"][i:i + 10])) for i in range(0, len(d["samples_ms"]) - 9, 10)]
            d["profile"] = [{k: (round(v, 1) if isinstance(v, float) else v) for k, v in x.items()} for x in prof]
            if bits is not None:
                d["wire_vs_fresh_render_differing_bits"] = bits
                check(bits == 0, f"{mode} {sid}: the panel was told to show the latest render (no missed update)", f"{bits} bits differ")
            res[sid] = d
            print(f"  {json.dumps({k: v for k, v in d.items() if k not in ('profile', 'samples_ms')})}", flush=True)
            if mode == "isolated":
                p = d["partial_only"]
                check(p["n"] >= 20, f"{mode} {sid}: sample size", str(p["n"]))
                if p["n"]:
                    check(p["median"] < 1000 and p["p90"] < 1000, f"{mode} {sid}: change-to-complete median and p90 < 1000 ms (partial refreshes)",
                          f"median {p['median']} p90 {p['p90']}")
    if a.no_set_now_playing:
        return out
    # set_now_playing must act at once: call -> first RECT on the wire. Start from an idle panel (big-clock changes once a
    # minute): a refresh already in flight would delay any render by up to 0.7 s whatever the engine does.
    rig.call("set_now_playing", None)
    rig.call("unpin_screen")
    rig.call("pin_screen", "big-clock")
    rig.until(lambda: rig.status()["current_screen"] == "big-clock", 20, "big-clock")
    time.sleep(8)
    rig.call("unpin_screen")
    rig.call("set_rotation", ["big-clock"], 3600)
    with rig.lock:
        n0 = len(rig.batches)
    t_call = time.monotonic()
    rig.call("set_now_playing", APPID)
    rig.until(lambda: len(rig.batches) > n0, 10, "batch after set_now_playing")
    with rig.lock:
        first = next(b for b in rig.batches[n0:])
    out["set_now_playing_to_first_rect_ms"] = round((first["t_first"] - t_call) * 1000, 1)
    time.sleep(12)   # the screen-switch full refresh (4 s) and, on the old engine, the second render with the real data
    with rig.lock:
        nb = [dict(b) for b in rig.batches[n0:] if b["t_ack"]]
    out["set_now_playing_batch_ack_ms"] = [round((b["t_ack"] - t_call) * 1000) for b in nb[:3]]
    print(f"  set_now_playing -> first RECT: {out['set_now_playing_to_first_rect_ms']} ms; batch ACKs at {out['set_now_playing_batch_ack_ms']} ms", flush=True)
    check(out["set_now_playing_to_first_rect_ms"] < 250, "set_now_playing reaches the wire within 250 ms (idle panel)", str(out["set_now_playing_to_first_rect_ms"]))
    rig.call("set_now_playing", None)
    time.sleep(5)
    return out


STEPS = {}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=["hello", "active", "idle", "packages", "ota", "robust", "soak", "latency", "cpu", "ghost"])
    ap.add_argument("--out", default=str(DECKY / "results"))
    ap.add_argument("--min-s", type=float, default=120.0, help="active: minimum seconds per screen")
    ap.add_argument("--max-s", type=float, default=420.0, help="active: give up on the update count after this")
    ap.add_argument("--updates", type=int, default=30, help="active: refreshes to collect per screen")
    ap.add_argument("--stims", type=int, default=30, help="latency: stimuli per screen and mode")
    ap.add_argument("--screens", default="telemetry,thermals-power,now-playing", help="latency: screens to measure (big-clock needs a stimulus: STIM)")
    ap.add_argument("--no-set-now-playing", action="store_true", help="latency: skip the closing set_now_playing check")
    ap.add_argument("--modes", default="live,isolated", help="latency: live (real data keeps changing) and/or isolated (other values frozen)")
    ap.add_argument("--cpu-s", type=float, default=60.0, help="cpu: measurement seconds per screen")
    ap.add_argument("--minutes", type=float, default=30.0, help="soak / ghost duration")
    ap.add_argument("--ghost-min", type=int, default=10, help="ghost: ghost_clear_minutes to test (0 = never)")
    ap.add_argument("--rollback-only", action="store_true", help="ota: skip the good install (panel must already run 1.0.1)")
    ap.add_argument("--verbose", action="store_true", help="print panel LOG lines")
    a = ap.parse_args()
    out = Path(a.out)
    if a.step in ("hello", "ota"):   # these build their own rig (ota needs the key and fake GitHub injected first)
        res = globals()[f"step_{a.step}"](a)
    else:
        fn = globals()[f"step_{a.step}"]
        rig = Rig(out, wire_log=a.verbose)
        try:
            rig.start()
            res = fn(a, rig)
        finally:
            rig.stop()
    res["checks"] = [{"check": l, "pass": ok, "detail": d} for l, ok, d in RESULTS]
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{a.step}{'-rollback' if getattr(a, 'rollback_only', False) else ''}.json").write_text(json.dumps(res, indent=2, default=str))
    bad = [l for l, ok, _ in RESULTS if not ok]
    print(f"RESULT {a.step}: {len(RESULTS) - len(bad)}/{len(RESULTS)} PASS" + (f"; FAILED: {bad}" if bad else ""), flush=True)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
