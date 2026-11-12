"""The real firmware OTA state machine (ota_usb.cpp, host-compiled with a fake flash) must
behave exactly like emulator.py: same reply bytes, same final state, on scripted and random
streams. Also runs the C++ selftest (power loss, rollback, mark-valid, ...)."""
import hashlib
import os
import random
import shutil
import struct
import subprocess

import pytest

from inkterface_core import proto as P
from inkterface_core.emulator import EmulatedPanel

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
FW = os.path.join(ROOT, "firmware")
SLOT = 64 * 1024
VER = "2.0.0"

pytestmark = pytest.mark.skipif(shutil.which("g++") is None, reason="g++ not available")


@pytest.fixture(scope="module")
def host_bin(tmp_path_factory):
    exe = str(tmp_path_factory.mktemp("ota") / "ota_host")
    subprocess.run(
        ["g++", "-std=c++17", "-O1", "-g", "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined",
         f"-I{FW}/src/ee05", f"{FW}/host_test/ota_host.cpp", f"{FW}/src/ee05/ota_usb.cpp",
         f"{FW}/src/ee05/usbproto.cpp", "-o", exe],
        check=True,
    )
    return exe


def test_cpp_selftest(host_bin):
    r = subprocess.run([host_bin, "--selftest"], capture_output=True, text=True, check=True)
    assert "OK" in r.stdout


class Stream:
    """Builds a host byte stream with auto-incrementing seq."""

    def __init__(self):
        self.buf = b""
        self.seq = 0

    def add(self, type_, payload=b""):
        self.buf += P.encode(type_, self.seq, payload)
        self.seq += 1
        return self

    def begin(self, img, ver=VER, size=None, sha=None):
        sha = sha if sha is not None else hashlib.sha256(img).digest()
        v = ver.encode() if isinstance(ver, str) else ver
        return self.add(P.T_OTA_BEGIN, struct.pack("<I", len(img) if size is None else size) + sha + v)

    def data(self, img, off, n):
        return self.add(P.T_OTA_DATA, struct.pack("<I", off) + img[off : off + n])

    def end(self):
        return self.add(P.T_OTA_END)

    def full(self, img, chunk=1000):
        self.begin(img)
        for off in range(0, len(img), chunk):
            self.data(img, off, min(chunk, len(img) - off))
        return self.end()


def img(n, salt=1):
    rnd = random.Random(salt)
    return b"\xe9" + bytes(rnd.getrandbits(8) for _ in range(n - 1))


def run_both(host_bin, tmp_path, stream: bytes, fail_at=None, corrupt_at=None, chunk=61):
    em = EmulatedPanel(fw_ver=VER, ota_slot=SLOT)
    em.ota_fail_write_at, em.ota_corrupt_at = fail_at, corrupt_at
    py = b"".join(em.feed(stream[i : i + chunk]) for i in range(0, len(stream), chunk))
    state = str(tmp_path / "state.txt")
    cmd = [host_bin, "--replay", str(SLOT), state]
    if fail_at is not None:
        cmd += ["--fail-write-at", str(fail_at)]
    if corrupt_at is not None:
        cmd += ["--corrupt-at", str(corrupt_at)]
    cpp = subprocess.run(cmd, input=stream, capture_output=True, check=True).stdout
    fields = dict(kv.split("=") for kv in open(state).read().split())
    assert cpp == py, "reply bytes differ between firmware and emulator"
    committed = em.ota_committed is not None
    assert fields["committed"] == str(int(committed)) and fields["reboot"] == str(int(committed))
    if committed:
        slot_img = em.installed + b"\xff" * (SLOT - len(em.installed))
        assert fields["image_sha256"] == hashlib.sha256(slot_img).hexdigest()
    else:
        assert fields["image_sha256"] == "-"
    return P.Decoder().feed(py), em


def replies(frames):
    return [(f.type, f.payload[1:] if f.type == P.T_ACK else f.payload[1:]) for f in frames if f.type in (P.T_ACK, P.T_NAK)]


def nak_reasons(frames):
    return [f.payload[1] for f in frames if f.type == P.T_NAK]


def hello():
    return Stream().add(P.T_HELLO)


def test_happy_path_identical(host_bin, tmp_path):
    s = hello().full(img(20_000))
    frames, em = run_both(host_bin, tmp_path, s.buf)
    assert frames[0].type == P.T_HELLO and P.parse_hello(frames[0].payload).caps & P.CAP_OTA
    assert all(f.type == P.T_ACK for f in frames[1:]) and em.ota_committed


def test_resume_and_restart_identical(host_bin, tmp_path):
    a, b = img(20_000, 1), img(20_000, 2)
    s = hello().begin(a).data(a, 0, 4000).data(a, 4000, 4000).begin(a).data(a, 8000, 4000)
    s.begin(a, ver="2.0.1").data(a, 0, 100)          # new params: restart
    s.begin(b, ver="2.0.1").data(b, 0, 1000).end()   # restart again, then END too early
    frames, _ = run_both(host_bin, tmp_path, s.buf)
    acks = [int.from_bytes(f.payload[1:5], "little") for f in frames if f.type == P.T_ACK and len(f.payload) == 5]
    assert acks == [0, 4000, 8000, 8000, 12000, 0, 100, 0, 1000]
    assert nak_reasons(frames) == [P.NAK_OTA_BAD_OFFSET]


def test_protocol_errors_identical(host_bin, tmp_path):
    a = img(10_000)
    sha = hashlib.sha256(a).digest()
    s = hello()
    s.add(P.T_OTA_DATA, struct.pack("<I", 0) + b"x")                # no session
    s.add(P.T_OTA_END)                                              # no session
    s.add(P.T_OTA_BEGIN, b"")                                       # BAD_LEN
    s.add(P.T_OTA_BEGIN, struct.pack("<I", 10) + sha)               # no version: BAD_LEN
    s.add(P.T_OTA_BEGIN, struct.pack("<I", 10) + sha + b"x" * 33)   # version too long
    s.begin(a, size=0)                                              # BAD_ARG
    s.begin(a, ver=b"\x07bad")                                      # BAD_ARG (control char)
    s.begin(a, size=SLOT + 1)                                       # TOO_BIG
    s.begin(a)                                                      # ok
    s.data(a, 0, 1000)
    s.add(P.T_OTA_DATA, struct.pack("<I", 4))                       # BAD_LEN (no data)
    s.data(a, 5000, 100)                                            # ahead: BAD_OFFSET
    s.data(a, 500, 1000)                                            # overlap: BAD_OFFSET
    s.data(a, 0, 1000)                                              # resend: ACK 1000
    s.add(P.T_OTA_DATA, struct.pack("<I", 1000) + b"\0" * 4092)     # 5092 < 10000: fine, ok write
    s.add(P.T_OTA_DATA, struct.pack("<I", 0xFFFFFFF0) + b"\0" * 64)  # huge offset
    s.add(P.T_OTA_END, b"x")                                        # BAD_LEN
    s.end()                                                         # incomplete: BAD_OFFSET
    frames, _ = run_both(host_bin, tmp_path, s.buf)
    assert nak_reasons(frames) == [
        P.NAK_OTA_NO_SESSION, P.NAK_OTA_NO_SESSION, P.NAK_BAD_LEN, P.NAK_BAD_LEN, P.NAK_BAD_LEN,
        P.NAK_BAD_ARG, P.NAK_BAD_ARG, P.NAK_OTA_TOO_BIG, P.NAK_BAD_LEN, P.NAK_OTA_BAD_OFFSET,
        P.NAK_OTA_BAD_OFFSET, P.NAK_OTA_BAD_OFFSET, P.NAK_BAD_LEN, P.NAK_OTA_BAD_OFFSET]


def test_overflow_past_declared_size_identical(host_bin, tmp_path):
    a = img(2000)
    s = hello().begin(a).data(a, 0, 1000)
    s.add(P.T_OTA_DATA, struct.pack("<I", 1000) + b"\0" * 1500)  # 2500 > 2000
    frames, _ = run_both(host_bin, tmp_path, s.buf)
    assert nak_reasons(frames) == [P.NAK_OTA_TOO_BIG]


def test_busy_after_commit_and_idempotent_end_identical(host_bin, tmp_path):
    a = img(3000)
    s = hello().full(a).end().begin(a).data(a, 0, 10)
    frames, em = run_both(host_bin, tmp_path, s.buf)
    assert nak_reasons(frames) == [P.NAK_OTA_BUSY, P.NAK_OTA_BUSY]
    assert em.ota_committed


def test_hash_mismatch_identical(host_bin, tmp_path):
    a = img(5000)
    frames, em = run_both(host_bin, tmp_path, hello().full(a).data(a, 0, 10).buf, corrupt_at=2500)
    assert nak_reasons(frames) == [P.NAK_OTA_HASH_MISMATCH, P.NAK_OTA_NO_SESSION]
    assert not em.ota_committed


def test_wrong_declared_hash_identical(host_bin, tmp_path):
    a = img(5000)
    s = hello().begin(a, sha=b"\x00" * 32)
    for off in range(0, 5000, 1000):
        s.data(a, off, 1000)
    s.end()
    frames, _ = run_both(host_bin, tmp_path, s.buf)
    assert nak_reasons(frames) == [P.NAK_OTA_HASH_MISMATCH]


def test_flash_write_failure_identical(host_bin, tmp_path):
    a = img(5000)
    frames, em = run_both(host_bin, tmp_path, hello().full(a).buf, fail_at=2500)
    assert P.NAK_OTA_FLASH_FAIL in nak_reasons(frames) and not em.ota_committed


def test_bad_image_magic_identical(host_bin, tmp_path):
    a = b"\x00" + img(3000)[1:]
    frames, em = run_both(host_bin, tmp_path, hello().full(a).buf)
    assert nak_reasons(frames) == [P.NAK_OTA_BAD_IMAGE] and not em.ota_committed


def test_hello_ack_identical(host_bin, tmp_path):
    s = hello().add(P.T_HELLO, struct.pack("<H", P.PROTO_VER) + VER.encode()).add(P.T_HEARTBEAT)
    frames, _ = run_both(host_bin, tmp_path, s.buf)
    assert [f.type for f in frames] == [P.T_HELLO, P.T_HELLO, P.T_ACK]


@pytest.mark.parametrize("seed", range(40))
def test_random_streams_identical(host_bin, tmp_path, seed):
    rnd = random.Random(seed)
    a, b = img(rnd.randrange(1, 9000), seed), img(rnd.randrange(1, 9000), seed + 100)
    s = hello()
    cur, off = a, 0
    for _ in range(rnd.randrange(5, 40)):
        k = rnd.random()
        if k < 0.12:
            cur, off = rnd.choice([a, b]), 0
            s.begin(cur, ver=rnd.choice([VER, "2.0.1"]))
        elif k < 0.55 and cur:
            n = min(rnd.randrange(1, 3000), len(cur) - off)
            if n > 0:
                s.data(cur, off, n)
                off += n
        elif k < 0.65:
            s.data(cur, rnd.randrange(0, len(cur)), rnd.randrange(1, 500)) if len(cur) > 1 else s.end()
        elif k < 0.75:
            s.add(P.T_OTA_DATA, struct.pack("<I", rnd.choice([0, off, off + 1, 2**32 - 1, len(cur)])) + bytes(rnd.randrange(0, 40)))
        elif k < 0.85:
            s.end()
        elif k < 0.9:
            s.add(P.T_OTA_BEGIN, bytes(rnd.randrange(0, 80)))
        else:
            s.add(rnd.choice([P.T_HEARTBEAT, P.T_HELLO, P.T_MODE]), bytes(rnd.randrange(0, 3)))
    run_both(host_bin, tmp_path, s.buf)
