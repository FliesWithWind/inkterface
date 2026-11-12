"""Compile firmware/host_test/uc8179_golden_host.cpp (uc8179_fast.h against a fake SPI) and compare the register
and LUT bytes it sends with the golden file. The tables are derived from the datasheet (docs/uc8179-tables.md);
a pass shows the panel is still driven byte for byte the same.
A deliberate waveform change regenerates the golden: <exe> > firmware/host_test/golden/uc8179_sequence.txt."""
import os
import re
import shutil
import subprocess
import zlib

import pytest

FW = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "firmware"))
GOLDEN = os.path.join(FW, "host_test", "golden", "uc8179_sequence.txt")
PINS = ["-DEPD_SCK=1", "-DEPD_MOSI=2", "-DEPD_CS=3", "-DEPD_DC=4", "-DEPD_RST=5", "-DEPD_BUSY=6"]


@pytest.fixture(scope="module")
def trace(tmp_path_factory):
    if shutil.which("g++") is None:
        pytest.skip("g++ not available")
    exe = str(tmp_path_factory.mktemp("uc") / "uc8179_golden_host")
    subprocess.run(["g++", "-std=c++17", "-O1", "-Wall", f"-I{FW}/host_test/fake",
                    f"-I{FW}/src/ee05", *PINS, f"{FW}/host_test/uc8179_golden_host.cpp", "-o", exe], check=True)
    return subprocess.run([exe], capture_output=True, text=True, check=True).stdout


def test_wire_bytes_match_golden(trace):
    with open(GOLDEN) as f:
        assert trace == f.read()


def test_trace_covers_every_mode(trace):
    """Guard the golden itself: it must contain all three LUT loads and every register the tables document."""
    cmds = re.findall(r"^C ([0-9a-f]{2})$", trace, re.M)
    for c in ("01", "00", "61", "15", "60", "82", "50", "20", "21", "22", "23", "24", "25", "04", "10", "13", "12", "02", "90", "91", "92", "e0", "e5"):
        assert c in cmds, c
    assert trace.count("# ") == 6 and "D 1f" in trace and "D 3f" in trace


def section(trace, name):
    m = re.search(rf"^# {re.escape(name)}[^\n]*\n(.*?)(?=^# |\Z)", trace, re.M | re.S)
    assert m, name
    return m.group(1)


def test_gray_is_the_makers_otp_waveform_with_no_lut_bytes(trace):
    """4-gray = LUT from OTP (PSR 1f), no PWR/LUT writes, TSFIX (e0 02) + forced temperature (e5 5f) BEFORE power on (PON senses the
    temperature once, before the booster is enabled: GDEY0583T81 datasheet p.13; with them after PON level 1 shows stripes), then both planes and the refresh."""
    g = section(trace, "gray4")
    cmds = re.findall(r"^C ([0-9a-f]{2})$", g, re.M)
    assert cmds == ["00", "61", "15", "60", "50", "e0", "e5", "04", "10", "13", "12", "02"], cmds
    assert "C 00\n  D 1f\n" in g and "C 50\n  D 21 07\n" in g
    assert "C 50\n  D 21 07\nC e0\n  D 02\nC e5\n  D 5f\nC 04\nC 10\n" in g


def test_mono_modes_never_touch_the_gray_registers(trace):
    """Only the gray section may write the forced-temperature registers: full, partial and the partial-after-gray stay as before."""
    for name in ("full", "partial 100,50", "partial 8,8", "partial after gray", "sleep"):
        assert not re.search(r"^C (e0|e5)$", section(trace, name), re.M), name


def _gray_planes():
    """The host harness fills fb then gray from one LCG; rebuild the two RAM planes the way the maker's demo packs them."""
    s = 1
    def nxt():
        nonlocal s
        s = (s * 1664525 + 1013904223) & 0xFFFFFFFF
        return s >> 24
    for _ in range(81 * 480):
        nxt()
    gray = [nxt() for _ in range(648 * 480 // 4)]
    px = [(b >> ((3 - p) * 2)) & 3 for b in gray for p in range(4)]
    def plane(bit):
        out = bytearray()
        for i in range(0, len(px), 8):
            v = 0
            for c in px[i:i + 8]:
                v = (v << 1) | ((c >> bit) & 1)
            out.append(v)
        return out
    return plane(1), plane(0)


def test_gray_planes_new_is_high_bit_old_is_low_bit(trace):
    """OTP waveform: NEW (0x13) carries the high bit, OLD (0x10) the low bit of the 2-bit code (0 black .. 3 white).
    Independent oracle: rebuild the planes in Python from the same pseudo-random image and compare CRC-32s."""
    hi, lo = _gray_planes()
    g = section(trace, "gray4")
    got = dict(re.findall(r"^C (10|13)\n  D\[(?:38880)\] crc32=([0-9a-f]{8})$", g, re.M))
    assert got == {"10": f"{zlib.crc32(bytes(lo)):08x}", "13": f"{zlib.crc32(bytes(hi)):08x}"}


def test_dcdc_hold_window(tmp_path):
    """The DC-DC hold window after a partial (Epd::holdMs, service()): powers off once, restarts per partial, cancelled by full/gray4."""
    if shutil.which("g++") is None:
        pytest.skip("g++ not available")
    exe = str(tmp_path / "uc8179_hold_host")
    subprocess.run(["g++", "-std=c++17", "-O1", "-Wall", f"-I{FW}/host_test/fake",
                    f"-I{FW}/src/ee05", *PINS, f"{FW}/host_test/uc8179_hold_host.cpp", "-o", exe], check=True)
    r = subprocess.run([exe], capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.rstrip().endswith("ALL OK"), r.stdout
