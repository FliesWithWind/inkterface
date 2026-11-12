"""Find and open the panel's serial port. Stdlib only (no pyserial: Decky's bundle lacks termios).

Never "first /dev/ttyACM*": other USB devices (game controllers) enumerate as ttyACM
too. Panels are matched by USB VID 0x303A (Espressif) read from sysfs; pass `serial` to
pick one of several.
"""
from __future__ import annotations

import os
import time

from . import proto as P
from .serialport import SerialPort

ESPRESSIF_VID = 0x303A
SYS_TTY = "/sys/class/tty"


def _usb_ids(name: str, sys_tty: str = SYS_TTY) -> tuple[int, str] | None:
    """(vid, serial number) of the USB device behind tty `name`, or None if it is not USB."""
    d = os.path.realpath(os.path.join(sys_tty, name, "device"))
    while True:
        try:
            with open(os.path.join(d, "idVendor")) as f:
                vid = int(f.read().strip(), 16)
        except (OSError, ValueError):
            parent = os.path.dirname(d)
            if parent == d:
                return None
            d = parent
            continue
        try:
            with open(os.path.join(d, "serial")) as f:
                return vid, f.read().strip()
        except OSError:
            return vid, ""


def find_panels(serial: str | None = None, sys_tty: str = SYS_TTY) -> list[str]:
    """Device paths of Espressif USB serial ports (optionally with a given USB serial number)."""
    try:
        names = os.listdir(sys_tty)
    except OSError:
        return []
    out = []
    for n in names:
        if not n.startswith(("ttyACM", "ttyUSB")):
            continue
        ids = _usb_ids(n, sys_tty)
        if ids and ids[0] == ESPRESSIF_VID and (not serial or serial.lower() in ids[1].lower()):
            out.append("/dev/" + n)
    return sorted(out, key=lambda p: (len(p), p))  # ttyACM2 < ttyACM10


def is_espressif(path: str, sys_tty: str = SYS_TTY) -> bool:
    ids = _usb_ids(os.path.basename(os.path.realpath(path)), sys_tty)
    return bool(ids and ids[0] == ESPRESSIF_VID)


def open_port(path: str) -> SerialPort:
    """Open without resetting the ESP32-S3 USB-Serial-JTAG chip.

    Linux asserts DTR+RTS together when a CDC port opens. The chip maps (DTR=0, RTS=1)
    to "pull EN low" (reset). SerialPort drops both lines in ONE ioctl, so the chip never
    sees that state ((1,1) -> (0,0)); lowering DTR alone first would reboot the panel
    (measured on hardware with pyserial's default open()).
    """
    return SerialPort.open(path, dtr=False, rts=False)


class Link:
    """Thin framed wrapper over a serial-like object (read(n, timeout)/write(b, timeout))."""

    def __init__(self, ser, log=None):
        self.ser = ser
        self.dec = P.Decoder()
        self.seq = 0
        self.log = log or (lambda text: None)

    def next_seq(self) -> int:
        s = self.seq
        self.seq = (self.seq + 1) & 0xFF
        return s

    def send(self, data: bytes) -> None:
        self.ser.write(data, 5.0)

    def poll(self, timeout: float = 0.0) -> list[P.Frame]:
        """Read whatever is available (waiting up to timeout for the first bytes)."""
        end = time.monotonic() + timeout
        frames: list[P.Frame] = []
        while True:
            chunk = self.ser.read(4096, max(0.0, min(0.05, end - time.monotonic())))
            if chunk:
                for f in self.dec.feed(chunk):
                    if f.type == P.T_LOG:
                        self.log(f.payload.decode("utf-8", "replace").rstrip())
                    else:
                        frames.append(f)
            if frames or time.monotonic() >= end:
                return frames

    def send_acked(self, items, window: int, timeout: float = 5.0):
        """Send (seq, wire_bytes) frames keeping <= `window` unacknowledged bytes in flight.

        The panel's USB RX queue silently drops bytes when it overflows, so this window
        (HELLO.rx_window) is the only flow control. Returns (ok, [(seq, reason)] naks).
        """
        pending = {}  # seq -> wire length, in send order
        naks, queue = [], list(items)
        deadline = time.monotonic() + timeout
        while queue or pending:
            while queue and (not pending or sum(pending.values()) + len(queue[0][1]) <= window):
                seq, data = queue.pop(0)
                self.send(data)
                pending[seq] = len(data)
            for f in self.poll(0.02):
                if f.type == P.T_ACK and f.payload and f.payload[0] in pending:
                    del pending[f.payload[0]]
                    deadline = time.monotonic() + timeout
                elif f.type == P.T_NAK and len(f.payload) >= 2 and f.payload[0] in pending:
                    del pending[f.payload[0]]
                    naks.append((f.payload[0], f.payload[1]))
            if time.monotonic() > deadline:
                return False, naks
        return not naks, naks
