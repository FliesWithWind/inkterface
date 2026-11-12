"""Differential test: the firmware decoders (idle_layout.cpp, idle_store.cpp, compiled with g++) must
agree with the Python oracle (inkterface_core.idle.decode / store) on every input: same field table,
same accept/reject, same first error code. Mutations re-fix the CRC so the deep checks run."""
import json
import os
import random
import shutil
import struct
import subprocess
import zlib

import pytest

import helpers as H
from inkterface_core.idle import fields as F
from inkterface_core.idle.compile import compile_layout
from inkterface_core.idle.decode import DecodeError, decode_layout
from inkterface_core.idle.store import StoreError, pack_store, parse_store

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
FW = os.path.join(ROOT, "firmware")
SCREENS = os.path.join(ROOT, "decky", "screens")

pytestmark = pytest.mark.skipif(shutil.which("g++") is None, reason="g++ not available")


@pytest.fixture(scope="module")
def exe(tmp_path_factory):
    out = str(tmp_path_factory.mktemp("ilh") / "idle_layout_host")
    subprocess.run(["g++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror", f"-I{FW}/src/ee05",
                    f"{FW}/host_test/idle_layout_host.cpp", f"{FW}/src/ee05/idle_layout.cpp",
                    f"{FW}/src/ee05/idle_store.cpp", f"{FW}/src/ee05/usbproto.cpp", "-o", out], check=True)
    return out


def stream(items):
    return b"".join(struct.pack("<I", len(i)) + i for i in items)


def run(exe, flag, items):
    r = subprocess.run([exe, flag], input=stream(items), capture_output=True, check=True)
    lines = r.stdout.decode().split()
    assert len(lines) == len(items)
    return lines


def py_code(b):
    try:
        decode_layout(b)
        return "OK"
    except DecodeError as e:
        return e.code


def refix(b):
    """recompute the trailing CRC so a body mutation reaches the structural checks"""
    b = bytearray(b)
    if len(b) >= 4:
        b[-4:] = struct.pack("<I", zlib.crc32(bytes(b[:-4])) & 0xFFFFFFFF)
    return bytes(b)


def corpus():
    out = []
    for sid in ("clock-weather", "clock-weather-gray4", "steam-stats", "minimal-clock"):
        scr = json.load(open(os.path.join(SCREENS, sid, "screen.json"), encoding="utf-8"))
        out.append(compile_layout(scr["idle"]))
    srcs = dict(H.CLOCK_SRC); srcs.update(H.WEATHER_SRC); srcs.update(H.STEAM_SRC); srcs.update(H.HTTP_SRC)
    ws = [
        {"type": "text", "x": 0, "y": 0, "w": 200, "h": 30, "font": "regular-18", "text": "{wx.current.temp:.1f}\u00b0 {st.persona}", "wrap": True},
        {"type": "number", "x": 0, "y": 40, "w": 200, "h": 40, "bind": "api.value", "format": "{}", "unit": "x", "font": "bold-28"},
        {"type": "bar", "x": 0, "y": 90, "w": 200, "h": 20, "bind": "st.players", "range": [0, 100]},
        {"type": "icon", "x": 0, "y": 120, "w": 40, "h": 40, "bind": "wx.forecast[2].code", "set": "weather"},
        {"type": "icon", "x": 50, "y": 120, "w": 40, "h": 40, "name": "gamepad"},
        {"type": "clock", "x": 0, "y": 170, "w": 300, "h": 100, "bind": "time.now", "format": "%I:%M %p", "font": "bold-80"},
    ]
    out.append(compile_layout(H.layout(ws, sources=srcs)))
    return out


def test_field_table_matches(exe):
    cpp = subprocess.run([exe, "--dump-fields"], capture_output=True, text=True, check=True).stdout
    assert cpp == F.field_table_text()


def test_corpus_accepted_by_both(exe):
    c = corpus()
    assert [py_code(b) for b in c] == ["OK"] * len(c)
    assert run(exe, "--decode-stream", c) == ["OK"] * len(c)


def test_truncation_everywhere(exe):
    for base in corpus()[:2]:
        items = [base[:n] for n in range(0, len(base), 7)] + [base + b"\x00", base[:-1]]
        assert run(exe, "--decode-stream", items) == [py_code(b) for b in items]


def test_header_and_crc_mutations(exe):
    base = corpus()[0]
    items = []
    for i in range(16):
        for v in (0, 1, 2, 0x7F, 0xFF):
            b = bytearray(base); b[i] = v
            items += [bytes(b), refix(bytes(b))]
    assert run(exe, "--decode-stream", items) == [py_code(b) for b in items]


def test_random_mutations_agree(exe):
    rnd = random.Random(1234)
    c = corpus()
    items = []
    for _ in range(3000):
        b = bytearray(rnd.choice(c))
        for _ in range(rnd.choice((1, 1, 2, 3))):
            i = rnd.randrange(len(b) - 4)
            b[i] = rnd.choice((0, 1, 2, 3, 0x7F, 0x80, 0xFF, rnd.randrange(256)))
        items.append(refix(bytes(b)))
    cpp = run(exe, "--decode-stream", items)
    py = [py_code(b) for b in items]
    bad = [(i, cpp[i], py[i]) for i in range(len(items)) if cpp[i] != py[i]]
    assert not bad, f"{len(bad)} disagreements, first: {bad[:5]}"
    # the fuzz must actually exercise rejections and acceptances
    assert len(set(py)) >= 5 and "OK" in py


def test_store_agrees(exe):
    c = corpus()
    good = pack_store(c[:3], active=1, rotate=True, rotate_s=30,
                      config={"wifi_ssid": "net", "wifi_pass": "pw", "tz": "UTC0", "lat": "1.5", "lon": "2.5",
                              "units": 1, "steam_key": "K", "steam_id": "7", "steam_appid": 730})
    items = [good, good[:-1], good[:10], b"", refix(good[:-1] + b"\x00")]
    rnd = random.Random(99)
    for _ in range(1500):
        b = bytearray(good)
        for _ in range(rnd.choice((1, 2))):
            b[rnd.randrange(len(b) - 4)] = rnd.choice((0, 1, 9, 0xFF, rnd.randrange(256)))
        items.append(refix(bytes(b)))
    cpp = run(exe, "--store-stream", items)

    def py_ok(b):
        try:
            parse_store(b)
            return True
        except (StoreError, DecodeError):
            return False
    bad = [(i, cpp[i]) for i, b in enumerate(items) if (cpp[i] == "OK") != py_ok(b)]
    assert not bad, f"{len(bad)} accept/reject disagreements: {bad[:5]}"
    assert cpp[0] == "OK"
