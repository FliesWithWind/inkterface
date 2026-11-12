"""Guard: frame types, NAK reasons and caps bits are the same numbers in C++ (usbproto.h,
idle_store.h) and Python (proto.py), and never collide (OTA 7..13, idle 14/15)."""
import re
from pathlib import Path

from inkterface_core import proto as P

FW = Path(__file__).resolve().parents[2] / "firmware" / "src" / "ee05"
HDR = (FW / "usbproto.h").read_text()


def _enum(name):
    body = re.search(r"enum " + name + r"\s*:\s*uint8_t\s*\{(.*?)\};", HDR, re.S).group(1)
    body = re.sub(r"//[^\n]*", "", body)
    return {k: int(v, 0) for k, v in re.findall(r"(\w+)\s*=\s*(0x[0-9a-fA-F]+|\d+)", body)}


def test_frame_types_match():
    cpp = _enum("Type")
    assert cpp, "parsed nothing"
    for k, v in cpp.items():
        assert getattr(P, k) == v, k
    assert len(set(cpp.values())) == len(cpp)


def test_nak_reasons_match_and_are_unique():
    cpp = _enum("Nak")
    assert {"NAK_OTA_BAD_OFFSET", "NAK_BAD_DATA", "NAK_STORAGE"} <= set(cpp)
    for k, v in cpp.items():
        assert getattr(P, k) == v, k
        assert P.NAK_NAMES[v] == k[len("NAK_"):], k
    assert len(set(cpp.values())) == len(cpp)
    assert set(P.NAK_NAMES) == set(cpp.values())  # no Python-only reason
    assert (P.NAK_BAD_DATA, P.NAK_STORAGE) == (14, 15)
    assert all(7 <= v <= 13 for k, v in cpp.items() if k.startswith("NAK_OTA_"))


def test_caps_bits_match_and_are_distinct():
    cpp = {k: 1 << int(b) for k, b in re.findall(r"constexpr uint32_t (CAP_\w+)\s*=\s*1u\s*<<\s*(\d+)", HDR)}
    assert set(cpp) == {"CAP_GRAY4", "CAP_IDLE_STORAGE", "CAP_OTA"}
    for k, v in cpp.items():
        assert getattr(P, k) == v, k
    assert len(set(cpp.values())) == len(cpp)


def test_idle_store_nak_constants_match_usbproto():
    store = (FW / "idle_store.h").read_text()
    got = dict(re.findall(r"(NAK_\w+_)\s*=\s*(\d+)", store))
    nak = _enum("Nak")
    assert int(got["NAK_BAD_DATA_"]) == nak["NAK_BAD_DATA"] == P.NAK_BAD_DATA
    assert int(got["NAK_STORAGE_"]) == nak["NAK_STORAGE"] == P.NAK_STORAGE
    assert int(got["NAK_BAD_LEN_"]) == nak["NAK_BAD_LEN"]
    assert int(got["NAK_BAD_ARG_"]) == nak["NAK_BAD_ARG"]
