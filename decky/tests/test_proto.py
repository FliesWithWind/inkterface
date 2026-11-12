import random

import pytest

from inkterface_core import proto as P


def test_golden_heartbeat():
    assert P.encode(P.T_HEARTBEAT, 7).hex() == "a55a020700001201028e"


def test_golden_refresh():
    assert P.refresh(3, full=True).hex() == "a55a110301000160bcd2ff"


def test_golden_rect():
    f = P.rect(1, 8, 2, 8, 2, bytes([0xFF, 0x0F]))
    assert f.hex() == "a55a10010a000800020008000200ff0fc3999b94"


def test_crc_known_vector():
    assert P.crc32(b"123456789") == 0xCBF43926


def test_roundtrip():
    d = P.Decoder()
    assert d.feed(P.encode(0x42, 255, b"abc")) == [P.Frame(0x42, 255, b"abc")]
    assert not d.pending


def test_seq_wraps_on_encode():
    assert P.encode(1, 256)[3] == 0


def test_max_payload_ok_and_over_rejected():
    big = bytes(range(256)) * 16
    assert len(big) == P.MAX_PAYLOAD
    assert P.Decoder().feed(P.encode(P.T_LOG, 1, big))[0].payload == big
    with pytest.raises(P.ProtocolError):
        P.encode(P.T_LOG, 1, big + b"x")


def test_oversize_len_in_header_rejected_without_waiting():
    d = P.Decoder()
    hdr = P.SYNC + bytes([P.T_RECT, 0]) + (0xFFFF).to_bytes(2, "little")
    good = P.heartbeat(5)
    assert d.feed(hdr + good) == [P.Frame(P.T_HEARTBEAT, 5)]
    assert d.bad_len == 1


def test_crc_mismatch_resyncs_to_next_frame():
    bad = bytearray(P.heartbeat(1))
    bad[-1] ^= 0x01
    d = P.Decoder()
    assert d.feed(bytes(bad) + P.heartbeat(2)) == [P.Frame(P.T_HEARTBEAT, 2)]
    assert d.crc_errors == 1


def test_corrupt_payload_byte_drops_only_that_frame():
    a, b, c = P.encode(P.T_LOG, 1, b"aaaa"), bytearray(P.encode(P.T_LOG, 2, b"bbbb")), P.encode(P.T_LOG, 3, b"cccc")
    b[7] ^= 0x40
    assert [f.seq for f in P.Decoder().feed(a + bytes(b) + c)] == [1, 3]


def test_garbage_prefix_including_lone_and_double_sync_bytes():
    junk = b"\x00\xffboot rom\r\n\xa5\xa5\xa5\x13"
    d = P.Decoder()
    assert [f.seq for f in d.feed(junk + P.heartbeat(9))] == [9]
    assert d.skipped >= len(junk) - 1


def test_sync_bytes_inside_payload_are_not_a_new_frame():
    payload = b"\xa5\x5a\xa5\x5a" * 10
    assert P.Decoder().feed(P.encode(P.T_LOG, 4, payload)) == [P.Frame(P.T_LOG, 4, payload)]


def test_false_header_with_big_len_does_not_swallow_real_frame_after_stall():
    d = P.Decoder()
    false_hdr = P.SYNC + bytes([P.T_RECT, 0]) + (2048).to_bytes(2, "little")
    assert d.feed(false_hdr + P.heartbeat(6)) == []  # waiting for 2048 bytes
    assert d.pending
    assert d.flush_stale() == [P.Frame(P.T_HEARTBEAT, 6)]
    assert not d.pending


@pytest.mark.parametrize("seed", range(5))
def test_stream_split_at_arbitrary_boundaries(seed):
    rnd = random.Random(seed)
    frames = [P.encode(rnd.choice([1, 2, 0x10, 0x7F]), i, rnd.randbytes(rnd.choice([0, 1, 13, 500, 4096]))) for i in range(12)]
    stream = b"junk\xa5" + b"".join(frames)
    want = P.Decoder().feed(stream)
    assert len(want) == 12
    d, got, i = P.Decoder(), [], 0
    while i < len(stream):
        n = rnd.choice([1, 1, 2, 3, 7, 64, 1000])
        got += d.feed(stream[i : i + n])
        i += n
    assert got == want


def test_every_single_split_point():
    stream = P.heartbeat(1) + P.rect(2, 0, 0, 8, 1, b"\xaa") + P.refresh(3)
    want = P.Decoder().feed(stream)
    for cut in range(len(stream) + 1):
        d = P.Decoder()
        assert d.feed(stream[:cut]) + d.feed(stream[cut:]) == want, cut


@pytest.mark.parametrize(
    "x,y,w,h",
    [(0, 0, 0, 8), (0, 0, 8, 0), (4, 0, 8, 8), (0, 0, 12, 8), (648, 0, 8, 1), (0, 480, 8, 1), (-8, 0, 8, 1), (0, 0, 656, 1)],
)
def test_rect_builder_rejects_bad_geometry(x, y, w, h):
    with pytest.raises(P.ProtocolError):
        P.rect_payload(x, y, w, h, bytes(max(0, h) * max(0, w // 8)))


def test_rect_builder_rejects_wrong_data_length_and_oversize():
    with pytest.raises(P.ProtocolError):
        P.rect_payload(0, 0, 8, 2, b"\x00")
    with pytest.raises(P.ProtocolError):
        P.rect_payload(0, 0, 648, 51, bytes(51 * 81))  # 4139 B > 4096


def test_rect_max_full_width_band_fits():
    assert len(P.rect_payload(0, 0, 648, 50, bytes(50 * 81))) == 8 + 4050 <= P.MAX_PAYLOAD


def test_hello_roundtrip():
    h = P.Hello(1, 4096, 8192, 648, 480, 0b10, 123456, "gabc123")
    assert P.parse_hello(P.hello_payload(h)) == h
