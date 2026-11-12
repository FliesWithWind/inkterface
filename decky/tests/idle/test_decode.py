"""Decoder byte-patch rejections (each header byte, truncation at every length, corrupt
crc) and the seeded random mutation fuzz: decode_layout only returns a dict or raises
DecodeError, never anything else."""
import random
import struct
import zlib

import pytest

from inkterface_core.idle import fields as F
from inkterface_core.idle.decode import decode_layout, DecodeError
from helpers import WEATHER_SRC, CLOCK_SRC, compile_ok


def sample():
    return compile_ok(
        [{"type": "clock", "x": 0, "y": 0, "w": 600, "h": 180, "format": "%H:%M", "font": "bold-160",
          "bind": "time.now"},
         {"type": "number", "x": 0, "y": 200, "w": 120, "h": 40, "bind": "wx.current.temp",
          "unit": " C", "font": "bold-28"},
         {"type": "text", "x": 0, "y": 250, "w": 300, "h": 24,
          "text": "max {wx.forecast[0].max:.0f}", "font": "regular-22"}],
        sources={**CLOCK_SRC, **WEATHER_SRC})


def _fix_crc(ba):
    struct.pack_into("<I", ba, len(ba) - 4, zlib.crc32(bytes(ba[:-4])) & 0xFFFFFFFF)


def test_good_layout_decodes():
    assert decode_layout(sample())["render_mode"] == "mono"


def test_corrupt_crc():
    ba = bytearray(sample())
    ba[-1] ^= 0xFF
    with pytest.raises(DecodeError) as e:
        decode_layout(bytes(ba))
    assert e.value.code == "BAD_CRC"


def test_bad_magic():
    ba = bytearray(sample())
    ba[0] = ord("Z")
    _fix_crc(ba)
    with pytest.raises(DecodeError) as e:
        decode_layout(bytes(ba))
    assert e.value.code == "BAD_MAGIC"


def test_bad_version():
    ba = bytearray(sample())
    ba[4] = 2
    _fix_crc(ba)
    with pytest.raises(DecodeError) as e:
        decode_layout(bytes(ba))
    assert e.value.code == "BAD_VERSION"


def test_too_short():
    with pytest.raises(DecodeError) as e:
        decode_layout(b"ILY1")
    assert e.value.code == "TOO_SHORT"


def test_bad_render_mode():
    ba = bytearray(sample())
    ba[5] = 7
    _fix_crc(ba)
    with pytest.raises(DecodeError) as e:
        decode_layout(bytes(ba))
    assert e.value.code == "BAD_HEADER"


def test_reserved_byte_nonzero():
    ba = bytearray(sample())
    ba[13] = 1
    _fix_crc(ba)
    with pytest.raises(DecodeError) as e:
        decode_layout(bytes(ba))
    assert e.value.code == "BAD_HEADER"


def test_zero_refresh():
    ba = bytearray(sample())
    struct.pack_into("<H", ba, 6, 0)
    _fix_crc(ba)
    with pytest.raises(DecodeError) as e:
        decode_layout(bytes(ba))
    assert e.value.code == "BAD_HEADER"


def test_length_mismatch():
    ba = bytearray(sample())
    struct.pack_into("<H", ba, 14, struct.unpack_from("<H", ba, 14)[0] + 1)  # pool_len+1
    _fix_crc(ba)
    with pytest.raises(DecodeError) as e:
        decode_layout(bytes(ba))
    assert e.value.code == "BAD_HEADER"


def test_truncation_at_every_length():
    good = sample()
    for cut in range(len(good)):
        with pytest.raises(DecodeError):
            decode_layout(good[:cut])


def test_each_header_byte_patch():
    """Flip each header byte, repair crc, and require decode_layout to either raise a
    DecodeError or return a self-consistent dict -- never crash or silently misparse.

    Not every flip is invalid: refresh_s/full_every are free u16s (1..65535), so flipping
    their bytes keeps a valid layout. The guarantee the panel relies on is only that the
    decoder answers with a dict XOR a DecodeError."""
    good = sample()
    free_u16_bytes = {6, 7, 8, 9}  # refresh_s (6,7) and full_every (8,9) accept any non-zero
    for i in range(F.HEADER_LEN):
        ba = bytearray(good)
        ba[i] ^= 0xFF
        _fix_crc(ba)
        try:
            out = decode_layout(bytes(ba))
        except DecodeError:
            continue
        # accepted: only legitimate for the free u16 fields
        assert i in free_u16_bytes and isinstance(out, dict), \
            f"header byte {i} flip was silently accepted"


def test_fuzz_only_dict_or_decode_error():
    rng = random.Random(1234)
    good = bytearray(sample())
    for _ in range(4000):
        ba = bytearray(good)
        nmut = rng.randint(1, 6)
        for _ in range(nmut):
            pos = rng.randrange(len(ba))
            ba[pos] = rng.randrange(256)
        # sometimes also truncate/extend
        if rng.random() < 0.2:
            ba = ba[: rng.randrange(1, len(ba) + 1)]
        if rng.random() < 0.1:
            ba += bytes(rng.randrange(1, 40))
        try:
            out = decode_layout(bytes(ba))
        except DecodeError as e:
            assert e.code in {"TOO_SHORT", "TOO_BIG", "BAD_MAGIC", "BAD_VERSION", "BAD_CRC",
                              "BAD_HEADER", "BAD_SOURCE", "BAD_WIDGET", "BAD_STRING", "BAD_REF"}
        else:
            assert isinstance(out, dict)
