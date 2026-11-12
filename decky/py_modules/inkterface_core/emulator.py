"""Fake EE05 panel: consumes the host byte stream, answers like the firmware, keeps
the 648x480 1-bit image, and writes PNG (or PBM without Pillow) on every REFRESH.

Behaviour mirrors firmware/src/ee05/usbproto.cpp (tests/test_cpp_diff.py checks the
reply bytes and final image against the real C++ code). Timeouts (host silence -> idle) are not emulated.
"""
from __future__ import annotations

import hashlib
import os
import struct
from dataclasses import dataclass, field

from . import proto as P
from .idle import fields as IF
from .idle.store import StoreError, parse_store

_INVERT = bytes(b ^ 0xFF for b in range(256))


@dataclass
class _OtaSession:
    size: int
    sha: bytes
    version: bytes
    data: bytearray = field(default_factory=bytearray)


class EmulatedPanel:
    """`ota_slot` (bytes) turns on OTA support, mirroring firmware/src/ee05/ota_usb.cpp with a
    fake flash; None keeps the spike behaviour (OTA_* -> NAK UNSUPPORTED).

    Test knobs: `ota_fail_write_at` (offset whose write fails), `ota_corrupt_at` (byte flipped
    after it is written), `reboot()` (apply a committed image), `watchdog_expire()` (the
    verify timer fires on an image nobody confirmed -> rollback)."""

    def __init__(self, out_dir: str | None = None, fw_ver: str = "emulator", caps: int = 0, idle_free: int = 0, rx_window: int = 8192, ota_slot: int | None = None):
        self.out_dir = out_dir
        # idle store (firmware idle_store.cpp Uploader): active only when caps has CAP_IDLE_STORAGE
        self.idle_store: bytes | None = None   # last committed ISTR blob (flash)
        self.idle_commits = 0
        self.idle_nak_with: int | None = None  # test knob: NAK the next final chunk with this reason (e.g. P.NAK_STORAGE)
        self.idle_frames: list[tuple[int, int, int]] = []   # (total, offset, len) of every IDLE_LAYOUT chunk seen
        self.mode_frames: list[bytes] = []     # raw MODE payloads seen
        self._idle_buf = bytearray()
        self._idle_total = 0
        self._idle_done = False
        self._idle_last: tuple[int, int] = (0, 0)
        self.ota_slot = ota_slot
        self.ota_fail_write_at: int | None = None
        self.ota_corrupt_at: int | None = None
        self._ota: _OtaSession | None = None
        self.ota_committed: _OtaSession | None = None  # set once OTA_END succeeded; cleared by reboot()
        self.installed: bytes | None = None  # the image in the inactive slot after a successful END
        self.pending_verify = False  # running a new image nobody has confirmed yet
        self.prev_fw_ver: str | None = None
        self.reboots = 0
        if ota_slot is not None:
            caps |= P.CAP_OTA
        self.fw_ver = fw_ver
        self.caps = caps
        self.idle_free = idle_free
        self.rx_window = rx_window
        self.fb = bytearray(P.FB_BYTES)  # shadow buffer, host writes land here
        self.glass = bytearray(P.FB_BYTES)  # what the e-paper shows (updated by REFRESH)
        self.mode = P.MODE_PANEL
        self.alive = False
        self.idle_timeout_s = 60  # default; MODE may change it (no clock here: timeouts are not emulated)
        self.refreshes: list[tuple[str, tuple[int, int, int, int], str | None]] = []
        self._dirty: tuple[int, int, int, int] | None = None  # x0, y0, x1, y1 exclusive
        self._dec = P.Decoder()
        self._tx = 0

    # -- public ---------------------------------------------------------------
    def feed(self, data: bytes) -> bytes:
        """Feed host bytes; return the bytes the panel would write back."""
        out = bytearray()
        for f in self._dec.feed(data):
            out += self._on_frame(f)
        return bytes(out)

    def flush_stale(self) -> bytes:
        """Panel's 100 ms stalled-half-frame timeout (usbproto.cpp Device::tick), made explicit."""
        out = bytearray()
        for f in self._dec.flush_stale():
            out += self._on_frame(f)
        return bytes(out)

    def save(self, path: str, glass: bool = True) -> str:
        return save_image(bytes(self.glass if glass else self.fb), path)

    # -- internals ------------------------------------------------------------
    def _send(self, type_: int, payload: bytes = b"") -> bytes:
        f = P.encode(type_, self._tx, payload)
        self._tx = (self._tx + 1) & 0xFF
        return f

    def _hello(self) -> bytes:
        h = P.Hello(P.PROTO_VER, P.MAX_PAYLOAD, self.rx_window, P.WIDTH, P.HEIGHT, self.caps, self.idle_free, self.fw_ver)
        return self._send(P.T_HELLO, P.hello_payload(h))

    def _ack(self, seq: int) -> bytes:
        return self._send(P.T_ACK, bytes([seq]))

    def _nak(self, seq: int, reason: int) -> bytes:
        return self._send(P.T_NAK, bytes([seq, reason]))

    def _on_frame(self, f: P.Frame) -> bytes:
        out = b""
        if not self.alive:
            self.alive = True
            if f.type != P.T_HELLO:
                out += self._hello()
        t = f.type
        if t == P.T_HELLO:
            if f.payload:
                self._hello_ack(f.payload)
            return out + self._hello()
        if t == P.T_HEARTBEAT:
            return out + self._ack(f.seq)
        if t == P.T_RECT:
            return out + self._rect(f)
        if t == P.T_REFRESH:
            return out + self._refresh(f)
        if t == P.T_MODE:
            return out + self._mode(f)
        if t in (P.T_OTA_BEGIN, P.T_OTA_DATA, P.T_OTA_END) and self.ota_slot is not None:
            return out + self._ota_frame(f)
        if t == P.T_IDLE_LAYOUT and self.caps & P.CAP_IDLE_STORAGE:
            return out + self._idle_layout(f)
        if t in (P.T_IDLE_LAYOUT, P.T_OTA_BEGIN, P.T_OTA_DATA, P.T_OTA_END):
            return out + self._nak(f.seq, P.NAK_UNSUPPORTED)
        if t in (P.T_ACK, P.T_NAK, P.T_LOG):
            return out
        return out + self._nak(f.seq, P.NAK_BAD_TYPE)

    # -- OTA ------------------------------------------------------------------
    def _ack_received(self, seq: int) -> bytes:
        return self._send(P.T_ACK, bytes([seq]) + struct.pack("<I", len(self._ota.data)))

    def _hello_ack(self, payload: bytes) -> None:
        """Host accepted our HELLO (proto_ver u16 + fw_ver): confirm a pending OTA image."""
        if self.pending_verify and len(payload) >= 2 and struct.unpack_from("<H", payload)[0] == P.PROTO_VER and payload[2:] == self.fw_ver.encode():
            self.pending_verify = False

    def _ota_frame(self, f: P.Frame) -> bytes:
        p, seq, t = f.payload, f.seq, f.type
        s = self._ota
        if t == P.T_OTA_BEGIN:
            if not 4 + 32 + 1 <= len(p) <= 4 + 32 + P.OTA_VERSION_MAX:
                return self._nak(seq, P.NAK_BAD_LEN)
            if self.ota_committed:
                return self._nak(seq, P.NAK_OTA_BUSY)
            size, sha, ver = struct.unpack_from("<I", p)[0], p[4:36], p[36:]
            if size == 0 or any(not 0x20 <= c < 0x7F for c in ver):
                return self._nak(seq, P.NAK_BAD_ARG)
            if size > self.ota_slot:
                return self._nak(seq, P.NAK_OTA_TOO_BIG)
            if not (s and (s.size, s.sha, s.version) == (size, sha, ver)):  # same params = resume
                self._ota = _OtaSession(size, sha, ver)
            return self._ack_received(seq)
        if t == P.T_OTA_DATA:
            if len(p) < 5:
                return self._nak(seq, P.NAK_BAD_LEN)
            if self.ota_committed:
                return self._nak(seq, P.NAK_OTA_BUSY)
            if s is None:
                return self._nak(seq, P.NAK_OTA_NO_SESSION)
            off, data, have = struct.unpack_from("<I", p)[0], p[4:], len(s.data)
            if off > have:
                return self._nak(seq, P.NAK_OTA_BAD_OFFSET)
            if off + len(data) > s.size:
                return self._nak(seq, P.NAK_OTA_TOO_BIG)
            if off + len(data) <= have:  # resent chunk we already hold
                return self._ack_received(seq)
            if off < have:
                return self._nak(seq, P.NAK_OTA_BAD_OFFSET)
            if self.ota_fail_write_at is not None and off <= self.ota_fail_write_at < off + len(data):
                self._ota = None
                return self._nak(seq, P.NAK_OTA_FLASH_FAIL)
            s.data += data
            if self.ota_corrupt_at is not None and off <= self.ota_corrupt_at < off + len(data):
                s.data[self.ota_corrupt_at] ^= 0x01
            return self._ack_received(seq)
        # OTA_END
        if p:
            return self._nak(seq, P.NAK_BAD_LEN)
        if self.ota_committed:
            return self._ack(seq)  # resent END: idempotent
        if s is None:
            return self._nak(seq, P.NAK_OTA_NO_SESSION)
        if len(s.data) != s.size:
            return self._nak(seq, P.NAK_OTA_BAD_OFFSET)
        self._ota = None
        if hashlib.sha256(s.data).digest() != s.sha:
            return self._nak(seq, P.NAK_OTA_HASH_MISMATCH)
        if s.data[0] != 0xE9:  # ESP image magic, what esp_ota_end checks first
            return self._nak(seq, P.NAK_OTA_BAD_IMAGE)
        self.ota_committed = s
        self.installed = bytes(s.data)
        return self._ack(seq)

    def reboot(self) -> None:
        """Panel resets: a committed image becomes the running one (pending until HELLO-acked)."""
        self.reboots += 1
        if self.ota_committed:
            self.prev_fw_ver, self.fw_ver = self.fw_ver, self.ota_committed.version.decode()
            self.pending_verify = True
            self.ota_committed = None
        self._ota = None
        self.alive = False
        self.mode = P.MODE_PANEL
        self._dec = P.Decoder()
        self._tx = 0
        self._dirty = None

    def watchdog_expire(self) -> None:
        """Verify timer fired: an unconfirmed image rolls back to the previous one and resets."""
        if self.pending_verify:
            self.fw_ver, self.prev_fw_ver, self.pending_verify = self.prev_fw_ver, None, False
            self.reboot()

    def _mode(self, f: P.Frame) -> bytes:
        """mode u8 [| idle_timeout_s u16 [| layout u8]]; every argument is checked before any is applied.
        The emulator has no store, so a layout index (other than 0xFF = keep) is refused like the
        firmware does without IdleHooks. Idle timing itself is not emulated."""
        pl = f.payload
        self.mode_frames.append(bytes(pl))
        if len(pl) not in (1, 3, 4):
            return self._nak(f.seq, P.NAK_BAD_LEN)
        if pl[0] > P.MODE_PANEL:
            return self._nak(f.seq, P.NAK_BAD_ARG)
        to = int.from_bytes(pl[1:3], "little") if len(pl) >= 3 else 0
        if to and not 15 <= to <= 3600:
            return self._nak(f.seq, P.NAK_BAD_ARG)
        if len(pl) == 4 and pl[3] != 0xFF:
            return self._nak(f.seq, P.NAK_BAD_ARG)
        if to:
            self.idle_timeout_s = to
        self.mode = pl[0]
        return self._ack(f.seq)

    def _idle_layout(self, f: P.Frame) -> bytes:
        """total u32 | offset u32 | data. Same decisions as idle::Uploader::onChunk; the final chunk validates the ISTR store."""
        p = f.payload
        if len(p) < 9:
            return self._nak(f.seq, P.NAK_BAD_LEN)
        total, off = struct.unpack_from("<II", p)
        n = len(p) - 8
        self.idle_frames.append((total, off, n))
        if not total or total > IF.MAX_STORE_BYTES or off + n > total:
            return self._nak(f.seq, P.NAK_BAD_LEN)
        if off == 0:
            self._idle_buf, self._idle_total, self._idle_done = bytearray(), total, False
        elif self._idle_done and total == self._idle_total and (off, n) == self._idle_last:
            return self._ack(f.seq)   # final chunk repeated after a lost ACK
        elif not self._idle_total or total != self._idle_total or self._idle_done:
            return self._nak(f.seq, P.NAK_BAD_ARG)
        data = bytes(p[8:])
        if off < len(self._idle_buf):   # retransmit
            ok = off + n <= len(self._idle_buf) and self._idle_buf[off:off + n] == data
            return self._ack(f.seq) if ok else self._nak(f.seq, P.NAK_BAD_ARG)
        if off > len(self._idle_buf):
            return self._nak(f.seq, P.NAK_BAD_ARG)
        self._idle_buf += data
        self._idle_last = (off, n)
        if len(self._idle_buf) < total:
            return self._ack(f.seq)
        blob, self._idle_buf, self._idle_total = bytes(self._idle_buf), bytearray(), 0
        try:
            parse_store(blob)
            bad = self.idle_nak_with
        except StoreError:
            bad = P.NAK_BAD_DATA
        if bad:
            self.idle_nak_with = None
            return self._nak(f.seq, bad)
        self.idle_store, self._idle_done, self._idle_total = blob, True, total
        self.idle_commits += 1
        return self._ack(f.seq)

    def _rect(self, f: P.Frame) -> bytes:
        if self.mode != P.MODE_HOST:
            return self._nak(f.seq, P.NAK_WRONG_MODE)
        if len(f.payload) < 8:
            return self._nak(f.seq, P.NAK_BAD_LEN)
        x, y, w, h = (int.from_bytes(f.payload[i : i + 2], "little") for i in (0, 2, 4, 6))
        if not w or not h or x % 8 or w % 8 or x + w > P.WIDTH or y + h > P.HEIGHT:
            return self._nak(f.seq, P.NAK_BAD_RECT)
        n = w // 8
        if len(f.payload) != 8 + h * n:
            return self._nak(f.seq, P.NAK_BAD_LEN)
        for r in range(h):
            o = (y + r) * P.STRIDE + x // 8
            self.fb[o : o + n] = f.payload[8 + r * n : 8 + (r + 1) * n]
        d = self._dirty
        self._dirty = (x, y, x + w, y + h) if d is None else (min(d[0], x), min(d[1], y), max(d[2], x + w), max(d[3], y + h))
        return self._ack(f.seq)

    def _refresh(self, f: P.Frame) -> bytes:
        if self.mode != P.MODE_HOST:
            return self._nak(f.seq, P.NAK_WRONG_MODE)
        if len(f.payload) != 1:
            return self._nak(f.seq, P.NAK_BAD_LEN)
        if f.payload[0] > 1:
            return self._nak(f.seq, P.NAK_BAD_ARG)
        full = f.payload[0] == 1
        box = (0, 0, P.WIDTH, P.HEIGHT) if full else self._dirty
        if box is not None:
            x0, y0, x1, y1 = box
            for r in range(y0, y1):
                o = r * P.STRIDE
                self.glass[o + x0 // 8 : o + x1 // 8] = self.fb[o + x0 // 8 : o + x1 // 8]
            path = None
            if self.out_dir:
                os.makedirs(self.out_dir, exist_ok=True)
                path = os.path.join(self.out_dir, f"refresh_{len(self.refreshes):04d}.png")
                path = self.save(path)
            self.refreshes.append(("F" if full else "P", (x0, y0, x1 - x0, y1 - y0), path))
        self._dirty = None
        return self._ack(f.seq)


def save_image(fb: bytes, path: str) -> str:
    """Write packed 1-bit frame (1 = black) as PNG via Pillow, else PBM (P4, 1 = black)."""
    try:
        from PIL import Image
    except ImportError:
        path = os.path.splitext(path)[0] + ".pbm"
        with open(path, "wb") as fh:
            fh.write(b"P4\n%d %d\n" % (P.WIDTH, P.HEIGHT) + bytes(fb))
        return path
    Image.frombytes("1", (P.WIDTH, P.HEIGHT), bytes(fb).translate(_INVERT)).save(path)
    return path
