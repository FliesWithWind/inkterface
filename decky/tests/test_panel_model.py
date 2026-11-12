"""Replay what the real host engine sends through the real firmware (usbproto.cpp + uc8179_fast.h) into a model of the UC8179
glass (firmware/host_test/panel_model_host.cpp), for a screen rotation A -> B -> A with every value and chart changing.

Checks the two things that decide whether the glass stays clean:
* digital sync: the OLD/NEW RAM planes agree with what the glass shows at every partial, the glass equals the firmware framebuffer
  after every refresh (nothing dropped at a window edge, a screen switch or a promoted partial);
* neutrality: a pixel inside a partial window that does not change must not be pushed by a field. The model sums
  (source level - VCOM) over the frames from the LUT and register bytes the driver really wrote."""
import json
import os
import random
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from inkterface_core import proto as P
from inkterface_core.emulator import EmulatedPanel
from inkterface_core.engine import Engine, EmulatedLink, Settings, load_screens

ROOT = Path(__file__).resolve().parents[2]
FW = ROOT / "firmware"
PINS = ["-DEPD_SCK=1", "-DEPD_MOSI=2", "-DEPD_CS=3", "-DEPD_DC=4", "-DEPD_RST=5", "-DEPD_BUSY=6"]

pytestmark = pytest.mark.skipif(shutil.which("g++") is None, reason="g++ not available")


def build(tmp_path_factory, name, *defs):
    exe = str(tmp_path_factory.mktemp(name) / "panel_model_host")
    subprocess.run(["g++", "-std=c++17", "-O1", "-Wall", f"-I{FW}/host_test/fake", f"-I{FW}/src/ee05", *PINS, *defs,
                    str(FW / "host_test/panel_model_host.cpp"), str(FW / "src/ee05/usbproto.cpp"), "-o", exe], check=True,
                   capture_output=True)
    return exe


@pytest.fixture(scope="module")
def model(tmp_path_factory):
    return build(tmp_path_factory, "pm")


@pytest.fixture(scope="module")
def old_vcom_model(tmp_path_factory):
    return build(tmp_path_factory, "pm_old", "-DINK_PARTIAL_VCOM=0x26")


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


class Data:
    """Registry stand-in whose values and chart histories keep moving, like live telemetry."""

    def __init__(self, base):
        self.d = dict(base)
        self.cb = []

    def subscribe(self, cb): self.cb.append(cb)
    def unsubscribe(self, cb): self.cb.remove(cb)
    def watch(self, keys): pass
    def set_now_playing(self, appid): pass
    def snapshot(self): return dict(self.d)

    def tick(self, rnd):
        for k, v in list(self.d.items()):
            if isinstance(v, list):
                self.d[k] = v[1:] + [max(0.0, v[-1] * rnd.uniform(0.9, 1.1))]
            elif isinstance(v, (int, float)):
                self.d[k] = max(0.0, v * rnd.uniform(0.9, 1.1))
        for cb in list(self.cb):
            cb()


def host_stream(order, seconds_each=12, seed=1):
    """Bytes the host writes while it rotates through `order` (screen ids), values changing every second."""
    screens, errs = load_screens(ROOT / "decky/screens")
    assert not errs
    base = {}
    for sid in set(order):
        base.update(json.loads((ROOT / f"decky/screens/{sid}/sample-data.json").read_text()))
    clock, data, rnd = Clock(), Data(base), random.Random(seed)
    links = []

    def connect():
        links.append(EmulatedLink(EmulatedPanel(), clock, 0.0, 0.0))
        return links[-1]

    eng = Engine(data, screens, Settings(rotation=list(order), rotation_interval_s=seconds_each), connect=connect, clock=clock,
                 wall=lambda: 5000 + clock.t)
    for i in range(int(seconds_each * len(order) / 0.05)):
        if i % 20 == 0:
            data.tick(rnd)
        eng.step()
        clock.t += 0.05
    return b"".join(P.encode(f.type, f.seq, f.payload) for f in links[0].written)


def run(exe, stream):
    out = subprocess.run([exe], input=stream, capture_output=True, check=True).stdout.decode()
    rows = [re.match(r"([FP]) (\d+) (\d+) (\d+) (\d+) stale_old=(\d+) fb_diff=(\d+) gray=(\d+) otp=(\d)", ln) for ln in out.splitlines()]
    rows = [m.groups() for m in rows if m]
    dose = {k: float(v) for k, v in re.findall(r"(\w+)=(-?[\d.]+)", out.splitlines()[-1])}
    return rows, dose


@pytest.fixture(scope="module")
def aba():
    return host_stream(["telemetry", "thermals-power", "telemetry"])


def test_screen_switch_a_b_a_keeps_ram_and_glass_in_sync(model, aba):
    rows, _ = run(model, aba)
    kinds = "".join(r[0] for r in rows)
    assert kinds.count("F") == 3 and kinds.count("P") > 30      # first frame + two switches are full refreshes
    for kind, x, y, w, h, stale, diff, gray, otp in rows:
        assert diff == "0", (kind, x, y, w, h)                  # glass == firmware framebuffer after every refresh
        assert gray == "0"
        if kind == "P":
            assert stale == "0"                                  # OLD RAM equals the glass for every pixel the controller sees
            assert int(x) % 8 == 0 and int(w) % 8 == 0           # window on byte columns


def test_unchanged_pixels_in_a_window_get_no_field(model, aba):
    _, dose = run(model, aba)
    swing = abs(dose["wk"])
    assert swing > 250                                           # a real full swing, so the numbers below are relative to something
    assert abs(dose["ww"]) < 0.02 * swing and abs(dose["kk"]) < 0.02 * swing   # left alone = under 2 % of a swing
    assert abs(dose["wk"] + dose["kw"]) < 0.02 * swing           # both directions equal: a pixel that goes there and back nets ~0


def test_the_old_vcom_fails_that_check(old_vcom_model, aba):
    """Control: VCOM_DC -2.0 V (the value before the fix) pushes every white pixel in a window ~13 % of a swing toward black per
    refresh and makes K->W 27 % weaker than W->K. The same stream, same checks above, must flag it."""
    _, dose = run(old_vcom_model, aba)
    swing = abs(dose["wk"])
    assert dose["ww"] > 0.1 * swing
    assert abs(dose["wk"] + dose["kw"]) > 0.2 * swing
