"""ISTR store: one blob holding every layout, the rotation, the active selection and
the panel config (docs/idle-mode.md "Store format"). An upload is atomic.

pack_store(layouts, *, active=0, rotate=False, rotate_s=60, config=None) -> bytes
parse_store(blob) -> dict   (strict; mirrors the firmware)

Every layout is validated with the Python decoder (decode.py) before packing, so a store
that would be refused by the panel is refused here first.
"""
from __future__ import annotations

import struct
import zlib

from . import fields as F
from .decode import DecodeError, decode_layout


class StoreError(ValueError):
    pass


# config tag -> (name, kind). kind: "s" text (no NUL), "u8", "u32".
_CONFIG_TAGS = {
    1: ("wifi_ssid", "s", 32),
    2: ("wifi_pass", "s", 63),
    3: ("tz", "s", 63),
    4: ("lat", "s", 15),
    5: ("lon", "s", 15),
    6: ("units", "u8", 1),
    7: ("steam_key", "s", 40),
    8: ("steam_id", "s", 20),
    9: ("steam_appid", "u32", 4),
}
_CONFIG_BY_NAME = {v[0]: (tag, v[1], v[2]) for tag, v in _CONFIG_TAGS.items()}

MIN_ROTATE_S = 10
MAX_ROTATE_S = 65535


def _encode_config(config: dict | None) -> bytes:
    if not config:
        return b""
    out = bytearray()
    seen = set()
    for name, value in config.items():
        if name not in _CONFIG_BY_NAME:
            raise StoreError(f"config: unknown key {name!r}")
        if name in seen:
            raise StoreError(f"config: duplicate key {name!r}")
        seen.add(name)
        tag, kind, limit = _CONFIG_BY_NAME[name]
        if kind == "s":
            if not isinstance(value, str):
                raise StoreError(f"config.{name}: must be a string")
            raw = value.encode("utf-8")
            if any(c < 0x20 or c == 0x7F for c in raw):
                raise StoreError(f"config.{name}: text value carries a control byte")
            if len(raw) > limit:
                raise StoreError(f"config.{name}: {len(raw)} bytes > {limit}")
        elif kind == "u8":
            if not isinstance(value, int) or isinstance(value, bool) or not (0 <= value <= 255):
                raise StoreError(f"config.{name}: must be a u8")
            raw = bytes([value])
        else:  # u32
            if not isinstance(value, int) or isinstance(value, bool) or not (0 <= value <= 0xFFFFFFFF):
                raise StoreError(f"config.{name}: must be a u32")
            raw = struct.pack("<I", value)
        if len(raw) > 255:
            raise StoreError(f"config.{name}: value too long for a TLV")
        out += bytes([tag, len(raw)]) + raw
    return bytes(out)


def pack_store(layouts, *, active: int = 0, rotate: bool = False,
               rotate_s: int = 60, config: dict | None = None) -> bytes:
    if not isinstance(layouts, (list, tuple)) or not (1 <= len(layouts) <= 8):
        raise StoreError(f"n_layouts must be 1..8, got {len(layouts) if hasattr(layouts, '__len__') else '?'}")
    n = len(layouts)
    if not (0 <= active < n):
        raise StoreError(f"active {active} out of range 0..{n - 1}")
    if rotate and not (MIN_ROTATE_S <= rotate_s <= MAX_ROTATE_S):
        raise StoreError(f"rotate_s {rotate_s} out of {MIN_ROTATE_S}..{MAX_ROTATE_S}")
    for i, lay in enumerate(layouts):
        if len(lay) > F.MAX_LAYOUT_BYTES:
            raise StoreError(f"layout[{i}]: {len(lay)} > {F.MAX_LAYOUT_BYTES}")
        try:
            decode_layout(lay)
        except DecodeError as e:
            raise StoreError(f"layout[{i}] invalid: {e}") from e

    cfg = _encode_config(config)
    flags = 1 if rotate else 0
    header = struct.pack("<4sBBBBHH", F.STORE_MAGIC, F.STORE_VER, n, active, flags,
                         rotate_s if rotate else rotate_s, len(cfg))
    body = bytearray(header)
    body += cfg
    for lay in layouts:
        body += struct.pack("<H", len(lay)) + bytes(lay)
    body += struct.pack("<I", zlib.crc32(bytes(body)) & 0xFFFFFFFF)
    if len(body) > F.MAX_STORE_BYTES:
        raise StoreError(f"store {len(body)} bytes > {F.MAX_STORE_BYTES}")
    return bytes(body)


def parse_store(blob: bytes) -> dict:
    blob = bytes(blob)
    n = len(blob)
    if n < 12 + 4:
        raise StoreError("store shorter than header + crc")
    if n > F.MAX_STORE_BYTES:
        raise StoreError(f"store {n} > {F.MAX_STORE_BYTES}")
    if blob[0:4] != F.STORE_MAGIC:
        raise StoreError(f"bad magic {blob[0:4]!r}")
    version = blob[4]
    if version != F.STORE_VER:
        raise StoreError(f"bad version {version}")
    want_crc = struct.unpack_from("<I", blob, n - 4)[0]
    got_crc = zlib.crc32(blob[: n - 4]) & 0xFFFFFFFF
    if want_crc != got_crc:
        raise StoreError(f"bad crc 0x{want_crc:08x} != 0x{got_crc:08x}")

    n_layouts = blob[5]
    active = blob[6]
    flags = blob[7]
    rotate_s = struct.unpack_from("<H", blob, 8)[0]
    config_len = struct.unpack_from("<H", blob, 10)[0]
    if not (1 <= n_layouts <= 8):
        raise StoreError(f"n_layouts {n_layouts} out of 1..8")
    if active >= n_layouts:
        raise StoreError(f"active {active} >= n_layouts {n_layouts}")
    if flags & ~1:
        raise StoreError(f"flags 0x{flags:02x}: reserved bits set")
    rotate = bool(flags & 1)
    if rotate and not (MIN_ROTATE_S <= rotate_s <= MAX_ROTATE_S):
        raise StoreError(f"rotate_s {rotate_s} out of range")

    off = 12
    end = n - 4
    if off + config_len > end:
        raise StoreError("config_len runs past the store")
    config = _parse_config(blob[off : off + config_len])
    off += config_len

    layouts = []
    for i in range(n_layouts):
        if off + 2 > end:
            raise StoreError(f"layout[{i}]: length field runs past the store")
        llen = struct.unpack_from("<H", blob, off)[0]
        off += 2
        if off + llen > end:
            raise StoreError(f"layout[{i}]: {llen} bytes run past the store")
        lay = blob[off : off + llen]
        off += llen
        try:
            decode_layout(lay)
        except DecodeError as e:
            raise StoreError(f"layout[{i}] invalid: {e}") from e
        layouts.append(lay)
    if off != end:
        raise StoreError(f"trailing bytes: parsed to {off}, crc at {end}")

    return {
        "n_layouts": n_layouts,
        "active": active,
        "rotate": rotate,
        "rotate_s": rotate_s,
        "config": config,
        "layouts": layouts,
    }


def _parse_config(raw: bytes) -> dict:
    out = {}
    seen = set()
    i = 0
    n = len(raw)
    while i < n:
        if i + 2 > n:
            raise StoreError("config: truncated TLV header")
        tag = raw[i]
        length = raw[i + 1]
        i += 2
        if i + length > n:
            raise StoreError(f"config: tag {tag} value runs past config section")
        value = raw[i : i + length]
        i += length
        if tag not in _CONFIG_TAGS:
            raise StoreError(f"config: unknown tag {tag}")
        if tag in seen:
            raise StoreError(f"config: duplicate tag {tag}")
        seen.add(tag)
        name, kind, limit = _CONFIG_TAGS[tag]
        if kind == "s":
            if length > limit:
                raise StoreError(f"config.{name}: {length} bytes > {limit}")
            if any(c < 0x20 or c == 0x7F for c in value):
                raise StoreError(f"config.{name}: contains a control byte")
            out[name] = value.decode("utf-8", "surrogateescape")
        elif kind == "u8":
            if length != 1:
                raise StoreError(f"config.{name}: length {length} != 1")
            out[name] = value[0]
        else:  # u32
            if length != 4:
                raise StoreError(f"config.{name}: length {length} != 4")
            out[name] = struct.unpack("<I", value)[0]
    return out
