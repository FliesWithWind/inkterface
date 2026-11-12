#!/usr/bin/env python3
"""Inkterface USB protocol v1 bench test against a real EE05 panel.

    python3 decky/tools/send_test.py                      # auto-detect (Espressif VID 303a)
    python3 decky/tools/send_test.py /dev/serial/by-id/usb-Espressif_...-if00
    python3 decky/tools/send_test.py --list

What it does, in order (each step prints [ OK ] / [FAIL] and a timing):
  1. HELLO handshake        panel must answer with proto version, size, firmware
  2. heartbeat round trips  10 x HEARTBEAT -> ACK latency
  3. throughput             5 x full 648x480 frame as RECT bands (no refresh), bytes/s
  4. test pattern           full frame + full refresh        (panel flashes, ~2-4 s)
  5. dirty rectangle        200x96 box + partial refresh     (~0.5-1.5 s)
  6. small rectangle        64x32 box  + partial refresh
  7. hands the display back to the panel's own screen (MODE_PANEL) unless --keep

The port is opened with DTR and RTS dropped together (stdlib SerialPort): on the
ESP32-S3 USB-Serial-JTAG DTR=0+RTS=1 resets the chip. Only ports with USB VID 0x303A are accepted unless --force.
"""
import argparse
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "py_modules"))

from inkterface_core import diff as D  # noqa: E402
from inkterface_core import proto as P  # noqa: E402
from inkterface_core import usb_link  # noqa: E402

FAILS = []


def ok(cond, label, detail=""):
    print(f"  [{' OK ' if cond else 'FAIL'}] {label}" + (f"  {detail}" if detail else ""))
    if not cond:
        FAILS.append(label)
    return cond


def pattern() -> bytearray:
    """Border, diagonals, 4x4-dithered gradient strip and a checkerboard. Pure Python."""
    fb = bytearray(P.FB_BYTES)

    def px(x, y, v=1):
        if 0 <= x < P.WIDTH and 0 <= y < P.HEIGHT:
            m = 0x80 >> (x & 7)
            fb[y * P.STRIDE + (x >> 3)] = (fb[y * P.STRIDE + (x >> 3)] | m) if v else (fb[y * P.STRIDE + (x >> 3)] & ~m)

    for x in range(P.WIDTH):
        for t in range(4):
            px(x, t); px(x, P.HEIGHT - 1 - t)
    for y in range(P.HEIGHT):
        for t in range(4):
            px(t, y); px(P.WIDTH - 1 - t, y)
    for i in range(P.HEIGHT - 8):
        px(4 + i * (P.WIDTH - 8) // P.HEIGHT, 4 + i); px(P.WIDTH - 5 - i * (P.WIDTH - 8) // P.HEIGHT, 4 + i)
    bayer = [[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]]
    for x in range(40, 600):
        level = (x - 40) * 16 // 560
        for y in range(60, 110):
            px(x, y, 1 if bayer[y & 3][x & 3] < level else 0)
    for y in range(340, 440):
        for x in range(40, 240):
            px(x, y, ((x // 10) + (y // 10)) & 1)
    return fb


class Panel:
    def __init__(self, link, window):
        self.link, self.window = link, window

    def expect(self, seqs, timeout, what):
        """Wait for ACKs for all seqs. Returns (ok, elapsed_s). Prints NAKs."""
        pending, t0, good = set(seqs), time.monotonic(), True
        while pending and time.monotonic() - t0 < timeout:
            for f in self.link.poll(0.02):
                if f.type == P.T_ACK and f.payload and f.payload[0] in pending:
                    pending.discard(f.payload[0])
                elif f.type == P.T_NAK:
                    s, r = P.parse_nak(f.payload)
                    print(f"    panel NAK seq={s} reason={P.NAK_NAMES.get(r, r)} during {what}")
                    pending.discard(s)
                    good = False
        if pending:
            print(f"    timeout after {timeout:.1f}s waiting for ACK seq {sorted(pending)} during {what}")
        return (good and not pending), time.monotonic() - t0

    def send_rects(self, rects, fb):
        """Windowed send (HELLO.rx_window bytes in flight). Returns (ok, seconds, wire_bytes)."""
        items = []
        for r in rects:
            s = self.link.next_seq()
            items.append((s, P.rect(s, *r, D.crop(fb, r))))
        t0 = time.monotonic()
        good, naks = self.link.send_acked(items, self.window)
        for s, r in naks:
            print(f"    panel NAK seq={s} reason={P.NAK_NAMES.get(r, r)} during RECT")
        if not good and not naks:
            print("    timeout waiting for RECT ACKs (bytes lost? check 'panel log' lines above)")
        return good, time.monotonic() - t0, sum(len(d) for _, d in items)

    def refresh(self, full):
        s = self.link.next_seq()
        t0 = time.monotonic()
        self.link.send(P.refresh(s, full))
        good, _ = self.expect([s], 20.0, "REFRESH")
        return good, time.monotonic() - t0


def handshake(link, timeout):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        link.send(P.hello_request(link.next_seq()))
        for f in link.poll(0.5):
            if f.type == P.T_HELLO:
                return P.parse_hello(f.payload), time.monotonic() - t0
    return None, time.monotonic() - t0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("port", nargs="?", help="serial device (default: auto-detect VID 303a)")
    ap.add_argument("--serial", help="USB serial number substring when several panels are attached")
    ap.add_argument("--list", action="store_true", help="list candidate ports and exit")
    ap.add_argument("--force", action="store_true", help="allow a port whose VID is not 303a")
    ap.add_argument("--keep", action="store_true", help="leave the host image on the panel (don't hand back)")
    ap.add_argument("--skip-refresh", action="store_true", help="transfer only, don't touch the e-paper")
    ap.add_argument("--partials", type=int, default=0, metavar="N",
                    help="also time N consecutive inverting partials for a 64x32 and a 200x96 box (median/p90)")
    ap.add_argument("--hello-timeout", type=float, default=10.0)
    a = ap.parse_args()

    cands = usb_link.find_panels(a.serial)
    if a.list:
        print("\n".join(cands) or "no Espressif (VID 303a) serial ports found")
        return 0
    path = a.port
    if not path:
        if len(cands) != 1:
            sys.exit(f"found {len(cands)} Espressif ports {cands}; pass the path or --serial")
        path = cands[0]
    if not a.force and not usb_link.is_espressif(path):
        sys.exit(f"{path} is not an Espressif (VID 303a) device; refusing (use --force to override)")

    print(f"port: {path} (opened without resetting the chip)")
    ser = usb_link.open_port(path)
    link = usb_link.Link(ser, log=lambda t: print(f"    panel log: {t}"))
    time.sleep(0.2)
    ser.reset_input_buffer()  # drop boot/ROM text so the first frame starts clean
    panel = None

    print("1. HELLO")
    hello, dt = handshake(link, a.hello_timeout)
    if not ok(hello is not None, "panel answered HELLO", f"({dt * 1000:.0f} ms)"):
        print("     no reply: wrong port? firmware not flashed? see docs/usb-protocol-v1.md 'Hardware test procedure'")
        return 1
    print(f"     proto v{hello.proto_ver}, {hello.width}x{hello.height}, max payload {hello.max_payload}, rx window {hello.rx_window}, "
          f"fw '{hello.fw_ver}', caps 0x{hello.caps:x}, idle flash free {hello.idle_flash_free}")
    panel = Panel(link, hello.rx_window)
    ok(hello.proto_ver == P.PROTO_VER, "protocol version matches host")
    ok((hello.width, hello.height) == (P.WIDTH, P.HEIGHT), "geometry is 648x480")

    print("2. heartbeat round trips (10)")
    rtts = []
    for _ in range(10):
        s = link.next_seq()
        t0 = time.monotonic()
        link.send(P.heartbeat(s))
        good, _ = panel.expect([s], 1.0, "HEARTBEAT")
        if good:
            rtts.append((time.monotonic() - t0) * 1000)
    ok(len(rtts) == 10, "all heartbeats ACKed")
    if rtts:
        print(f"     RTT ms: min {min(rtts):.1f}  median {statistics.median(rtts):.1f}  max {max(rtts):.1f}")

    s = link.next_seq()
    link.send(P.mode(s, P.MODE_HOST))
    ok(panel.expect([s], 1.0, "MODE")[0], "MODE_HOST accepted (panel stops drawing its own screens)")

    full_rects = D.split_rect((0, 0, P.WIDTH, P.HEIGHT))
    pat = bytes(pattern())

    print("3. throughput: 5 x full frame (RECT only, no refresh)")
    rates = []
    for _ in range(5):
        good, dt, n = panel.send_rects(full_rects, pat)
        if good:
            rates.append(n / dt)
    ok(len(rates) == 5, "5 full frames transferred + ACKed")
    if rates:
        wire = len(full_rects) * (P.HDR_LEN + P.CRC_LEN + 8) + P.FB_BYTES
        print(f"     wire bytes/frame {wire}; throughput KB/s: min {min(rates) / 1000:.0f}  "
              f"median {statistics.median(rates) / 1000:.0f}  max {max(rates) / 1000:.0f}  "
              f"=> full frame in {wire / statistics.median(rates) * 1000:.0f} ms (median)")

    if not a.skip_refresh:
        print("4. test pattern: full frame + FULL refresh (panel will flash)")
        good, dt, _ = panel.send_rects(full_rects, pat)
        ok(good, "full frame transferred", f"({dt * 1000:.0f} ms)")
        good, dt = panel.refresh(full=True)
        ok(good, "full refresh ACKed", f"({dt:.2f} s)")

        print("5. dirty rectangle: 200x96 box + PARTIAL refresh")
        new = bytearray(pat)
        for y in range(190, 286):
            for bx in range(28, 53):  # x 224..423
                new[y * P.STRIDE + bx] ^= 0xFF
        rects = D.diff_rects(pat, bytes(new))
        print(f"     diff -> {rects}")
        good, dt, n = panel.send_rects(rects, bytes(new))
        ok(good, "rect transferred", f"({n} wire bytes, {dt * 1000:.1f} ms)")
        good, dt = panel.refresh(full=False)
        ok(good, "partial refresh ACKed", f"({dt:.2f} s)")

        print("6. small rectangle: 64x32 box + PARTIAL refresh")
        new2 = bytearray(new)
        for y in range(20, 52):
            for bx in range(70, 78):
                new2[y * P.STRIDE + bx] = 0xFF
        rects = D.diff_rects(bytes(new), bytes(new2))
        print(f"     diff -> {rects}")
        good, dt, n = panel.send_rects(rects, bytes(new2))
        ok(good, "rect transferred", f"({n} wire bytes, {dt * 1000:.1f} ms)")
        good, dt = panel.refresh(full=False)
        ok(good, "partial refresh ACKed", f"({dt:.2f} s)")

    if a.partials and not a.skip_refresh:
        print(f"6b. {a.partials} consecutive partials per size (box inverts every time: worst case)")
        cur = bytearray(new2)
        for label, (bx0, y0, wb, h) in (("64x32", (70, 20, 8, 32)), ("200x96", (28, 190, 25, 96))):
            times = []
            for _ in range(a.partials):
                nxt = bytearray(cur)
                for y in range(y0, y0 + h):
                    for bx in range(bx0, bx0 + wb):
                        nxt[y * P.STRIDE + bx] ^= 0xFF
                good, _dt, _n = panel.send_rects(D.diff_rects(bytes(cur), bytes(nxt)), bytes(nxt))
                good2, dt = panel.refresh(full=False)
                ok(good and good2, f"{label} partial {len(times) + 1}") if not (good and good2) else None
                times.append(dt)
                cur = nxt
            t = sorted(times)
            p90 = t[min(len(t) - 1, -(-90 * len(t) // 100) - 1)]
            print(f"     {label}: n={len(t)} median {statistics.median(t) * 1000:.0f} ms  p90 {p90 * 1000:.0f} ms  "
                  f"min {t[0] * 1000:.0f}  max {t[-1] * 1000:.0f}")

    if not a.keep:
        print("7. hand display back to panel")
        s = link.next_seq()
        link.send(P.mode(s, P.MODE_PANEL))
        ok(panel.expect([s], 1.0, "MODE")[0], "MODE_PANEL accepted (panel redraws its own screen)")
    ser.close()

    print()
    print("RESULT:", "ALL OK" if not FAILS else f"{len(FAILS)} FAILED -> " + "; ".join(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
