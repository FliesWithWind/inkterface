"""Run the real firmware parser/device (usbproto.cpp, host-compiled) against the
Python emulator on the same byte stream: replies and final image must match."""
import os
import random
import shutil
import subprocess

import pytest

from inkterface_core import diff as D
from inkterface_core import proto as P
from inkterface_core.emulator import EmulatedPanel

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
FW = os.path.join(ROOT, "firmware")

pytestmark = pytest.mark.skipif(shutil.which("g++") is None, reason="g++ not available")


@pytest.fixture(scope="module")
def host_bin(tmp_path_factory):
    exe = str(tmp_path_factory.mktemp("cpp") / "usbproto_host")
    subprocess.run(
        ["g++", "-std=c++17", "-O1", "-Wall", "-Wextra", f"-I{FW}/src/ee05",
         f"{FW}/host_test/usbproto_host.cpp", f"{FW}/src/ee05/usbproto.cpp", "-o", exe],
        check=True,
    )
    return exe


def run_cpp(exe, stream, chunk, tmp_path):
    g, e = str(tmp_path / "glass.bin"), str(tmp_path / "events.txt")
    out = subprocess.run([exe, str(chunk), g, e], input=stream, capture_output=True, check=True).stdout
    return out, open(g, "rb").read(), open(e).read().split("\n")[:-1]


def test_cpp_selftest(host_bin):
    r = subprocess.run([host_bin, "--selftest"], capture_output=True, text=True, check=True)
    assert "OK" in r.stdout


def build_stream(seed):
    rnd = random.Random(seed)
    s = bytearray(b"\x00boot\xa5\xa5junk")
    seq = 0

    def add(b):
        s.extend(b)

    add(P.hello_request(seq)); seq += 1
    add(P.rect(seq, 0, 0, 8, 1, b"\xff")); seq += 1  # WRONG_MODE
    add(P.mode(seq, P.MODE_HOST)); seq += 1
    add(P.mode(seq, 7)); seq += 1  # BAD_ARG
    prev = bytes(P.FB_BYTES)
    for _ in range(3):
        b = bytearray(prev)
        for _ in range(rnd.choice([1, 50, 3000])):
            b[rnd.randrange(P.FB_BYTES)] ^= 1 << rnd.randrange(8)
        new = bytes(b)
        for r in D.diff_rects(prev, new):
            add(P.rect(seq, *r, D.crop(new, r))); seq += 1
            if rnd.random() < 0.3:  # inject damage between frames
                bad = bytearray(P.heartbeat(seq)); bad[-2] ^= 0x55
                add(bytes(bad)); add(rnd.randbytes(rnd.randrange(1, 20)).replace(b"\xa5", b"\x00"))
        add(P.refresh(seq, full=rnd.random() < 0.3)); seq += 1
        add(P.heartbeat(seq)); seq += 1
        prev = new
    for payload in (b"\x00\x00\x00\x00\x00\x00\x08\x00", b"\x04\x00\x00\x00\x08\x00\x01\x00\xff", b"\x00"):
        add(P.encode(P.T_RECT, seq, payload)); seq += 1
    add(P.refresh(seq)); seq += 1  # nothing dirty
    add(P.encode(0x55, seq)); seq += 1
    add(P.encode(P.T_OTA_DATA, seq, b"x")); seq += 1
    for t, pl in ((P.T_REFRESH, b""), (P.T_REFRESH, b"\x02"), (P.T_REFRESH, b"\x00\x00")):
        add(P.encode(t, seq, pl)); seq += 1  # BAD_LEN / BAD_ARG
    add(P.encode(P.T_LOG, seq, b"ignored")); seq += 1
    # MODE extension: timeout / layout arguments (hooks-less firmware: no layouts exist)
    for payload in (b"\x00\x14\x00", b"\x00\x0e\x00", b"\x00\x11\x0e", b"\x00\x00\x00\xff", b"\x00\x00\x00\x00",
                    b"\x00\x14", b"\x00\x14\x00\x00\x00", b"", b"\x02\x14\x00", b"\x00\x0f\x00\xff"):
        add(P.encode(P.T_MODE, seq, payload)); seq += 1
    add(P.encode(P.T_IDLE_LAYOUT, seq, b"x")); seq += 1  # UNSUPPORTED without hooks
    add(P.encode(P.T_IDLE_LAYOUT, seq, bytes(12))); seq += 1
    add(P.mode(seq, P.MODE_PANEL)); seq += 1
    add(P.heartbeat(seq)); seq += 1  # a pinned panel still ACKs heartbeats
    add(P.refresh(seq))  # WRONG_MODE
    return bytes(s)


@pytest.mark.parametrize("seed", range(3))
@pytest.mark.parametrize("chunk", [1, 7, 64, 100000])
def test_cpp_matches_emulator(host_bin, tmp_path, seed, chunk):
    stream = build_stream(seed)
    em = EmulatedPanel(fw_ver="host-test")
    want = em.feed(stream)
    got, glass, events = run_cpp(host_bin, stream, chunk, tmp_path)
    assert got == want
    assert glass == bytes(em.glass)
    assert [f"{k} {x} {y} {w} {h}" for k, (x, y, w, h), _ in em.refreshes] == events
