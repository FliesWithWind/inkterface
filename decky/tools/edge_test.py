#!/usr/bin/env python3
"""Hostile-input checks for the panel's USB parser (does not refresh the e-paper).

    python3 decky/tools/edge_test.py [port] [--slow]

Sends garbage, bad CRCs, split writes, bad rectangles and a stalled half frame, and
checks the panel still answers correctly afterwards. --slow also waits out the idle timeout
(set to its 15 s minimum for the test, then restored to 60 s). Same port rules as send_test.py. Leaves the panel in MODE_PANEL (pinned idle).
"""
import argparse
import os
import struct
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "py_modules"))

from inkterface_core import proto as P  # noqa: E402
from inkterface_core import usb_link  # noqa: E402

FAILS = []


def ok(cond, label):
    print(f"  [{' OK ' if cond else 'FAIL'}] {label}")
    if not cond:
        FAILS.append(label)


def collect(link, want, timeout=1.0):
    """Frames (excluding HELLO) until `want` of them arrived or timeout."""
    out, end = [], time.monotonic() + timeout
    while len(out) < want and time.monotonic() < end:
        out += [f for f in link.poll(0.05) if f.type != P.T_HELLO]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("port", nargs="?")
    ap.add_argument("--slow", action="store_true")
    a = ap.parse_args()
    path = a.port or (usb_link.find_panels() or [None])[0]
    if not path or not usb_link.is_espressif(path):
        sys.exit("no Espressif (VID 303a) port; pass the path")
    link = usb_link.Link(usb_link.open_port(path), log=lambda t: print(f"    panel log: {t.strip()}"))
    link.ser.reset_input_buffer()
    link.send(P.hello_request(0))
    if not any(f.type == P.T_HELLO for f in link.poll(5.0)):
        sys.exit("no HELLO")
    s = lambda: link.next_seq()  # noqa: E731

    def one(frame, label, expect_type, expect_payload=None, timeout=1.0):
        got = collect(link, 1, timeout)
        ok(len(got) == 1 and got[0].type == expect_type and (expect_payload is None or got[0].payload == expect_payload), label + (f"  got {[(g.type, g.payload.hex()) for g in got]}" if not got or got[0].type != expect_type else ""))

    link.send(P.mode(s(), P.MODE_HOST)); collect(link, 1)

    print("garbage / corruption")
    q = s(); link.send(b"\x00boot rom\r\n\xa5\xa5\xa5\x13" + P.heartbeat(q)); one(None, "garbage prefix then HEARTBEAT -> ACK", P.T_ACK, bytes([q]))
    bad = bytearray(P.heartbeat(s())); bad[-1] ^= 1
    q = s(); link.send(bytes(bad) + P.heartbeat(q)); one(None, "bad CRC frame ignored, next frame ACKed", P.T_ACK, bytes([q]))
    q = s(); link.send(P.SYNC + bytes([P.T_RECT, 0, 0xFF, 0xFF]) + P.heartbeat(q)); one(None, "len=65535 header rejected, next frame ACKed", P.T_ACK, bytes([q]))
    q = s(); link.send(P.SYNC + bytes([P.T_RECT, 0, 0x00, 0x10])); time.sleep(0.3)
    q2 = s(); link.send(P.heartbeat(q2)); one(None, "stalled 4096-byte half frame dropped after 100 ms", P.T_ACK, bytes([q2]))

    print("split writes")
    q = s(); frame = P.rect(q, 0, 0, 16, 2, b"\xaa\x55\xaa\x55")
    for b in frame:
        link.send(bytes([b])); time.sleep(0.002)
    one(None, "RECT written one byte at a time -> ACK", P.T_ACK, bytes([q]))

    print("bad rectangles")
    for label, pl, reason in [
        ("zero width", b"\x00\x00\x00\x00\x00\x00\x08\x00", P.NAK_BAD_RECT),
        ("zero height", b"\x00\x00\x00\x00\x08\x00\x00\x00", P.NAK_BAD_RECT),
        ("unaligned x", b"\x04\x00\x00\x00\x08\x00\x01\x00\xff", P.NAK_BAD_RECT),
        ("unaligned w", b"\x00\x00\x00\x00\x0c\x00\x01\x00\xff\xff", P.NAK_BAD_RECT),
        ("past right edge", b"\x88\x02\x00\x00\x08\x00\x01\x00\xff", P.NAK_BAD_RECT),
        ("past bottom edge", b"\x00\x00\xdf\x01\x08\x00\x02\x00\xff\xff", P.NAK_BAD_RECT),
        ("short data", b"\x00\x00\x00\x00\x08\x00\x02\x00\xff", P.NAK_BAD_LEN),
    ]:
        q = s(); link.send(P.encode(P.T_RECT, q, pl)); one(None, f"{label} -> NAK", P.T_NAK, bytes([q, reason]))
    q = s(); link.send(P.encode(0x55, q)); one(None, "unknown type -> NAK BAD_TYPE", P.T_NAK, bytes([q, P.NAK_BAD_TYPE]))
    q = s(); link.send(P.encode(P.T_OTA_BEGIN, q)); one(None, "OTA_BEGIN empty -> NAK BAD_LEN (OTA firmware; UNSUPPORTED on the spike)", P.T_NAK, bytes([q, P.NAK_BAD_LEN]))

    print("mode handling")
    link.send(P.mode(s(), P.MODE_PANEL)); collect(link, 1)
    q = s(); link.send(P.rect(q, 0, 0, 8, 1, b"\xff")); one(None, "RECT in MODE_PANEL -> NAK WRONG_MODE", P.T_NAK, bytes([q, P.NAK_WRONG_MODE]))

    if a.slow:
        print("host silence -> idle -> wake (waits ~20 s)")
        link.send(P.mode_ex(s(), P.MODE_HOST, 15)); collect(link, 1)       # shortest allowed timeout
        time.sleep(16)
        q = s(); link.send(P.rect(q, 0, 0, 8, 1, b"\xff"))
        got, end = [], time.monotonic() + 12.0  # the panel draws its idle screen first (full refresh, ~4 s)
        while time.monotonic() < end and len(got) < 2:
            got += link.poll(0.05)
        ok([f.type for f in got][:2] == [P.T_HELLO, P.T_ACK], "after silence: panel idled, the RECT woke it: new session (HELLO) then ACK")
        link.send(P.mode_ex(s(), P.MODE_HOST, 60)); collect(link, 1)       # restore the default (persisted in NVS)
        q = s(); link.send(P.encode(P.T_MODE, q, struct.pack("<BH", P.MODE_HOST, 14))); one(None, "timeout 14 s refused", P.T_NAK, bytes([q, P.NAK_BAD_ARG]))
        q = s(); link.send(P.encode(P.T_MODE, q, bytes([0, 20]))); one(None, "2-byte MODE -> NAK BAD_LEN", P.T_NAK, bytes([q, P.NAK_BAD_LEN]))

    link.send(P.mode(s(), P.MODE_PANEL)); collect(link, 1)
    print("\nRESULT:", "ALL OK" if not FAILS else f"{len(FAILS)} FAILED")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
