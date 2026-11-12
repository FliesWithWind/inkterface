"""Inkterface USB-CDC protocol v1, host side. Pure Python, no Decky imports.

Wire format and rules: docs/usb-protocol-v1.md. Panel side: firmware/src/ee05/usbproto.cpp.

    A5 5A | type u8 | seq u8 | len u16 LE | payload | crc32 LE over type..payload
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

SYNC = b"\xa5\x5a"
PROTO_VER = 1
MAX_PAYLOAD = 4096
HDR_LEN = 6
CRC_LEN = 4
MAX_FRAME = HDR_LEN + MAX_PAYLOAD + CRC_LEN

WIDTH, HEIGHT = 648, 480
STRIDE = WIDTH // 8
FB_BYTES = STRIDE * HEIGHT

T_HELLO, T_HEARTBEAT, T_ACK, T_NAK = 0x01, 0x02, 0x03, 0x04
T_RECT, T_REFRESH, T_MODE = 0x10, 0x11, 0x12
T_IDLE_LAYOUT = 0x20
T_OTA_BEGIN, T_OTA_DATA, T_OTA_END = 0x30, 0x31, 0x32
T_LOG = 0x7F

NAK_BAD_TYPE, NAK_BAD_LEN, NAK_BAD_RECT, NAK_BAD_ARG, NAK_WRONG_MODE, NAK_UNSUPPORTED = 1, 2, 3, 4, 5, 6
(NAK_OTA_BAD_OFFSET, NAK_OTA_FLASH_FAIL, NAK_OTA_HASH_MISMATCH, NAK_OTA_TOO_BIG, NAK_OTA_BUSY,
 NAK_OTA_NO_SESSION, NAK_OTA_BAD_IMAGE) = 7, 8, 9, 10, 11, 12, 13
NAK_NAMES = {1: "BAD_TYPE", 2: "BAD_LEN", 3: "BAD_RECT", 4: "BAD_ARG", 5: "WRONG_MODE", 6: "UNSUPPORTED",
             7: "OTA_BAD_OFFSET", 8: "OTA_FLASH_FAIL", 9: "OTA_HASH_MISMATCH", 10: "OTA_TOO_BIG",
             11: "OTA_BUSY", 12: "OTA_NO_SESSION", 13: "OTA_BAD_IMAGE"}

MODE_HOST, MODE_PANEL = 0, 1
CAP_GRAY4, CAP_IDLE_STORAGE, CAP_OTA = 1 << 0, 1 << 1, 1 << 2
OTA_VERSION_MAX = 32
OTA_MAX_CHUNK = MAX_PAYLOAD - 4  # OTA_DATA payload: offset u32 + data

HEARTBEAT_INTERVAL_S = 5.0
HOST_TIMEOUT_S = 60.0  # default idle timeout (MODE can change it, 15..3600 s)


class ProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class Frame:
    type: int
    seq: int
    payload: bytes = b""


def crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def encode(type: int, seq: int, payload: bytes = b"") -> bytes:
    if len(payload) > MAX_PAYLOAD:
        raise ProtocolError(f"payload {len(payload)} > {MAX_PAYLOAD}")
    body = struct.pack("<BBH", type, seq & 0xFF, len(payload)) + bytes(payload)
    return SYNC + body + struct.pack("<I", crc32(body))


class Decoder:
    """Streaming decoder; mirrors usbproto::Parser (rewind-by-one on bad len/CRC)."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self.crc_errors = 0
        self.bad_len = 0
        self.skipped = 0

    @property
    def pending(self) -> bool:
        return bool(self._buf)

    def feed(self, data: bytes) -> list[Frame]:
        self._buf += data
        return self._drain()

    def flush_stale(self) -> list[Frame]:
        """Give up on a stalled partial frame (same rewind as the panel's flushStale)."""
        out: list[Frame] = []
        while self._buf:
            del self._buf[0]
            out += self._drain()
        return out

    def _drain(self) -> list[Frame]:
        buf, out = self._buf, []
        while True:
            i = buf.find(SYNC)
            if i < 0:
                keep = 1 if buf[-1:] == b"\xa5" else 0
                self.skipped += len(buf) - keep
                del buf[: len(buf) - keep]
                return out
            self.skipped += i
            del buf[:i]
            if len(buf) < HDR_LEN:
                return out
            type_, seq, n = struct.unpack_from("<BBH", buf, 2)
            if n > MAX_PAYLOAD:
                self.bad_len += 1
                del buf[0]
                continue
            total = HDR_LEN + n + CRC_LEN
            if len(buf) < total:
                return out
            if crc32(bytes(buf[2 : HDR_LEN + n])) != struct.unpack_from("<I", buf, HDR_LEN + n)[0]:
                self.crc_errors += 1
                del buf[0]  # rewind: a real frame may start inside this one
                continue
            out.append(Frame(type_, seq, bytes(buf[HDR_LEN : HDR_LEN + n])))
            del buf[:total]


# --- payload builders / parsers ------------------------------------------------

def rect_payload(x: int, y: int, w: int, h: int, bits: bytes) -> bytes:
    """RECT payload. bits = h rows of w/8 bytes, MSB first, 1 = black."""
    if w <= 0 or h <= 0 or x % 8 or w % 8 or x < 0 or y < 0 or x + w > WIDTH or y + h > HEIGHT:
        raise ProtocolError(f"bad rect {x},{y},{w},{h}")
    if len(bits) != h * (w // 8):
        raise ProtocolError(f"rect data {len(bits)} != {h * (w // 8)}")
    p = struct.pack("<HHHH", x, y, w, h) + bytes(bits)
    if len(p) > MAX_PAYLOAD:
        raise ProtocolError(f"rect payload {len(p)} > {MAX_PAYLOAD}; split it (diff.split_rect)")
    return p


def rect(seq: int, x: int, y: int, w: int, h: int, bits: bytes) -> bytes:
    return encode(T_RECT, seq, rect_payload(x, y, w, h, bits))


def refresh(seq: int, full: bool = False) -> bytes:
    return encode(T_REFRESH, seq, bytes([1 if full else 0]))


def mode(seq: int, m: int) -> bytes:
    return encode(T_MODE, seq, bytes([m]))


def heartbeat(seq: int) -> bytes:
    return encode(T_HEARTBEAT, seq)


def hello_request(seq: int) -> bytes:
    return encode(T_HELLO, seq)


@dataclass(frozen=True)
class Hello:
    proto_ver: int
    max_payload: int
    rx_window: int
    width: int
    height: int
    caps: int
    idle_flash_free: int
    fw_ver: str


def hello_payload(h: Hello) -> bytes:
    return struct.pack("<HHHHHII", h.proto_ver, h.max_payload, h.rx_window, h.width, h.height, h.caps, h.idle_flash_free) + h.fw_ver.encode()[:64]


def parse_hello(payload: bytes) -> Hello:
    if len(payload) < 18:
        raise ProtocolError("short HELLO")
    pv, mp, win, w, h, caps, free = struct.unpack_from("<HHHHHII", payload)
    return Hello(pv, mp, win, w, h, caps, free, payload[18:].decode("utf-8", "replace"))


def parse_ack(payload: bytes) -> int:
    return payload[0]


def parse_nak(payload: bytes) -> tuple[int, int]:
    return payload[0], payload[1]


# --- OTA (docs/usb-protocol-v1.md "OTA") ---------------------------------------

def ota_begin(seq: int, size: int, sha256: bytes, version: str) -> bytes:
    v = version.encode("ascii")
    if len(sha256) != 32 or not 1 <= len(v) <= OTA_VERSION_MAX or not 0 < size < 1 << 32:
        raise ProtocolError("bad OTA_BEGIN arguments")
    return encode(T_OTA_BEGIN, seq, struct.pack("<I", size) + sha256 + v)


def ota_data(seq: int, offset: int, data: bytes) -> bytes:
    if not 0 < len(data) <= OTA_MAX_CHUNK:
        raise ProtocolError(f"OTA chunk must be 1..{OTA_MAX_CHUNK} bytes")
    return encode(T_OTA_DATA, seq, struct.pack("<I", offset) + bytes(data))


def ota_end(seq: int) -> bytes:
    return encode(T_OTA_END, seq)


def hello_ack(seq: int, h: Hello) -> bytes:
    """Host accepts the panel's HELLO: proto_ver u16 + the fw_ver string it saw. Tells a freshly
    OTA-flashed panel to mark its image valid (else the bootloader rolls back)."""
    return encode(T_HELLO, seq, struct.pack("<H", h.proto_ver) + h.fw_ver.encode()[:64])


def parse_ack_received(payload: bytes) -> int | None:
    """Bytes the panel holds, from the ACK of OTA_BEGIN / OTA_DATA (`seq u8, received u32`)."""
    return struct.unpack_from("<I", payload, 1)[0] if len(payload) >= 5 else None

# --- idle mode (docs/idle-mode.md "Transport") --------------------------------
# IDLE_LAYOUT (0x20) carries the whole ISTR store in chunks; MODE (0x12) gained
# optional idle_timeout_s and layout fields. These extend the v1 spike without
# changing any existing RECT/REFRESH/MODE(1-byte) behaviour above.

# NAK reasons added by idle mode. 1..6 are base, 7..13 belong to OTA (above), so idle takes 14/15.
NAK_BAD_DATA, NAK_STORAGE = 14, 15
NAK_NAMES[NAK_BAD_DATA] = "BAD_DATA"
NAK_NAMES[NAK_STORAGE] = "STORAGE"


def idle_layout_payload(total: int, offset: int, data: bytes) -> bytes:
    """IDLE_LAYOUT chunk payload: total u32 | offset u32 | data."""
    if total < 0 or total > 0xFFFFFFFF or offset < 0 or offset > 0xFFFFFFFF:
        raise ProtocolError(f"bad idle chunk total={total} offset={offset}")
    p = struct.pack("<II", total, offset) + bytes(data)
    if len(p) > MAX_PAYLOAD:
        raise ProtocolError(f"idle chunk payload {len(p)} > {MAX_PAYLOAD}")
    return p


def idle_layout(seq: int, total: int, offset: int, data: bytes) -> bytes:
    return encode(T_IDLE_LAYOUT, seq, idle_layout_payload(total, offset, data))


def mode_payload(m: int, idle_timeout_s: int = 0, layout: int = 0xFF) -> bytes:
    """MODE payload: mode u8 [| idle_timeout_s u16 [| layout u8]].

    A bare 1-byte payload (idle_timeout_s == 0 and layout == 0xFF) matches the v1
    spike exactly. idle_timeout_s 0 keeps the panel's current value (default 60 s,
    persisted in NVS), else 15..3600; layout 0xFF keeps the stored selection, else
    an index into the stored layouts.
    """
    if m not in (MODE_HOST, MODE_PANEL):
        raise ProtocolError(f"bad mode {m}")
    if idle_timeout_s and not (15 <= idle_timeout_s <= 3600):
        raise ProtocolError(f"idle_timeout_s {idle_timeout_s} out of 15..3600")
    if not (0 <= layout <= 0xFF):
        raise ProtocolError(f"layout {layout} out of range")
    if idle_timeout_s == 0 and layout == 0xFF:
        return bytes([m])
    if layout == 0xFF:
        return struct.pack("<BH", m, idle_timeout_s)
    return struct.pack("<BHB", m, idle_timeout_s, layout)


def mode_ex(seq: int, m: int, idle_timeout_s: int = 0, layout: int = 0xFF) -> bytes:
    return encode(T_MODE, seq, mode_payload(m, idle_timeout_s, layout))
