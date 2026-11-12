"""ISTR store round trip and every store rejection."""
import struct
import zlib

import pytest

from inkterface_core.idle import fields as F
from inkterface_core.idle.store import pack_store, parse_store, StoreError
from helpers import compile_ok


def a_layout(text="hi"):
    return compile_ok([{"type": "text", "x": 0, "y": 0, "w": 100, "h": 20, "text": text}])


def test_round_trip_all_config():
    cfg = {"wifi_ssid": "home", "wifi_pass": "secret", "tz": "CET-1", "lat": "52.1",
           "lon": "4.3", "units": 1, "steam_key": "K" * 32, "steam_id": "7656119",
           "steam_appid": 570}
    blob = pack_store([a_layout("a"), a_layout("b")], active=1, rotate=True,
                      rotate_s=45, config=cfg)
    p = parse_store(blob)
    assert p["n_layouts"] == 2 and p["active"] == 1 and p["rotate"] is True
    assert p["rotate_s"] == 45 and p["config"] == cfg
    assert len(p["layouts"]) == 2


def test_minimal_store():
    p = parse_store(pack_store([a_layout()]))
    assert p["n_layouts"] == 1 and p["active"] == 0 and p["rotate"] is False


def test_bad_crc():
    blob = bytearray(pack_store([a_layout()]))
    blob[-1] ^= 0xFF
    with pytest.raises(StoreError, match="crc"):
        parse_store(bytes(blob))


def test_bad_layout_inside():
    bad = bytearray(a_layout())
    bad[4] = 9  # version wrong
    struct.pack_into("<I", bad, len(bad) - 4, zlib.crc32(bytes(bad[:-4])) & 0xFFFFFFFF)
    with pytest.raises(StoreError, match="invalid"):
        pack_store([bytes(bad)])


def test_dup_tag_on_parse():
    # hand-build a config with tag 1 twice
    cfg = bytes([1, 1, ord("a"), 1, 1, ord("b")])
    body = struct.pack("<4sBBBBHH", F.STORE_MAGIC, 1, 1, 0, 0, 0, len(cfg)) + cfg
    lay = a_layout()
    body += struct.pack("<H", len(lay)) + lay
    body += struct.pack("<I", zlib.crc32(body) & 0xFFFFFFFF)
    with pytest.raises(StoreError, match="duplicate tag"):
        parse_store(body)


def test_unknown_tag_on_parse():
    cfg = bytes([200, 1, ord("a")])
    body = struct.pack("<4sBBBBHH", F.STORE_MAGIC, 1, 1, 0, 0, 0, len(cfg)) + cfg
    lay = a_layout()
    body += struct.pack("<H", len(lay)) + lay
    body += struct.pack("<I", zlib.crc32(body) & 0xFFFFFFFF)
    with pytest.raises(StoreError, match="unknown tag"):
        parse_store(body)


def _max_layout():
    # the largest layout the compiler will emit: exactly MAX_LAYOUT_BYTES (8192).
    return compile_ok([{"type": "text", "x": 0, "y": 0, "w": 640, "h": 20, "text": "a" * 8142}])


def test_over_49152_refused():
    # eight 8192-byte layouts = 65536 > 49152, so the store cap must trip.
    big = _max_layout()
    assert len(big) == F.MAX_LAYOUT_BYTES
    with pytest.raises(StoreError, match="> 49152"):
        pack_store([big] * 8)


def test_store_just_under_cap_ok():
    # five 8192-byte layouts (+ framing) is ~41K, well under 49152.
    big = _max_layout()
    blob = pack_store([big] * 5)
    assert len(blob) <= F.MAX_STORE_BYTES
    assert parse_store(blob)["n_layouts"] == 5


def test_n_layouts_zero_and_nine():
    with pytest.raises(StoreError, match="1..8"):
        pack_store([])
    with pytest.raises(StoreError, match="1..8"):
        pack_store([a_layout()] * 9)


def test_active_out_of_range():
    with pytest.raises(StoreError, match="active"):
        pack_store([a_layout()], active=1)


def test_rotate_s_range():
    with pytest.raises(StoreError, match="rotate_s"):
        pack_store([a_layout()], rotate=True, rotate_s=5)
    with pytest.raises(StoreError, match="rotate_s"):
        pack_store([a_layout()], rotate=True, rotate_s=70000)


def test_over_long_config_value():
    with pytest.raises(StoreError, match="wifi_ssid"):
        pack_store([a_layout()], config={"wifi_ssid": "x" * 33})
    with pytest.raises(StoreError, match="steam_id"):
        pack_store([a_layout()], config={"steam_id": "9" * 21})


def test_config_control_bytes_rejected():
    for bad in ("a\x00b", "a\nb", "a\x7fb"):
        with pytest.raises(StoreError, match="control byte"):
            pack_store([a_layout()], config={"tz": bad})


def test_unknown_config_key_on_pack():
    with pytest.raises(StoreError, match="unknown key"):
        pack_store([a_layout()], config={"nope": "x"})


def test_parse_trailing_bytes():
    blob = bytearray(pack_store([a_layout()]))
    blob.insert(len(blob) - 4, 0)  # extra byte before crc span... rebuild crc to isolate trailing check
    # recompute crc so only the trailing-bytes check can trip
    struct.pack_into("<I", blob, len(blob) - 4, zlib.crc32(bytes(blob[:-4])) & 0xFFFFFFFF)
    with pytest.raises(StoreError):
        parse_store(bytes(blob))


def test_bad_magic_and_version():
    blob = bytearray(pack_store([a_layout()]))
    blob[0] = ord("X")
    struct.pack_into("<I", blob, len(blob) - 4, zlib.crc32(bytes(blob[:-4])) & 0xFFFFFFFF)
    with pytest.raises(StoreError, match="magic"):
        parse_store(bytes(blob))
    blob2 = bytearray(pack_store([a_layout()]))
    blob2[4] = 2
    struct.pack_into("<I", blob2, len(blob2) - 4, zlib.crc32(bytes(blob2[:-4])) & 0xFFFFFFFF)
    with pytest.raises(StoreError, match="version"):
        parse_store(bytes(blob2))
