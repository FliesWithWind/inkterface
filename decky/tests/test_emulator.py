import random

import pytest

from inkterface_core import diff as D
from inkterface_core import proto as P
from inkterface_core.emulator import EmulatedPanel


def frames_of(b):
    return P.Decoder().feed(b)


def host_session(em):
    """MODE host; returns next seq."""
    out = frames_of(em.feed(P.mode(0, P.MODE_HOST)))
    assert [f.type for f in out] == [P.T_HELLO, P.T_ACK]  # first frame of session -> HELLO first
    return 1


def rand_frame(rnd, density=0.5):
    return bytes(rnd.getrandbits(8) if rnd.random() < density else 0 for _ in range(P.FB_BYTES))


def mutate(rnd, fb, n):
    b = bytearray(fb)
    for _ in range(n):
        b[rnd.randrange(P.FB_BYTES)] ^= 1 << rnd.randrange(8)
    return bytes(b)


def send_diff(em, prev, new, seq):
    rects = D.diff_rects(prev, new)
    for r in rects:
        reply = frames_of(em.feed(P.rect(seq, *r, D.crop(new, r))))
        assert [(f.type, f.payload) for f in reply] == [(P.T_ACK, bytes([seq & 0xFF]))]
        seq += 1
    return rects, seq


def test_hello_request():
    em = EmulatedPanel(fw_ver="t1")
    (f,) = frames_of(em.feed(P.hello_request(5)))
    h = P.parse_hello(f.payload)
    assert (h.proto_ver, h.width, h.height, h.max_payload, h.rx_window, h.fw_ver) == (1, 648, 480, 4096, 8192, "t1")


def test_rect_before_mode_is_wrong_mode():
    em = EmulatedPanel()
    em.feed(P.hello_request(0))
    (f,) = frames_of(em.feed(P.rect(1, 0, 0, 8, 1, b"\xff")))
    assert (f.type, f.payload) == (P.T_NAK, bytes([1, P.NAK_WRONG_MODE]))


@pytest.mark.parametrize(
    "payload,reason",
    [
        (b"\x00\x00\x00\x00\x00\x00\x08\x00", P.NAK_BAD_RECT),  # w=0
        (b"\x00\x00\x00\x00\x08\x00\x00\x00", P.NAK_BAD_RECT),  # h=0
        (b"\x04\x00\x00\x00\x08\x00\x01\x00\xff", P.NAK_BAD_RECT),  # x unaligned
        (b"\x00\x00\x00\x00\x0c\x00\x01\x00\xff\xff", P.NAK_BAD_RECT),  # w unaligned
        (b"\x88\x02\x00\x00\x08\x00\x01\x00\xff", P.NAK_BAD_RECT),  # x=648 out of bounds
        (b"\x00\x00\xdf\x01\x08\x00\x02\x00\xff\xff", P.NAK_BAD_RECT),  # y+h > 480
        (b"\x00\x00\x00\x00\x08\x00\x02\x00\xff", P.NAK_BAD_LEN),  # data too short
        (b"\x00\x00\x00", P.NAK_BAD_LEN),  # truncated header
    ],
)
def test_bad_rects_nak(payload, reason):
    em = EmulatedPanel()
    host_session(em)
    (f,) = frames_of(em.feed(P.encode(P.T_RECT, 9, payload)))
    assert (f.type, f.payload) == (P.T_NAK, bytes([9, reason]))
    assert not any(em.fb)


def test_unknown_and_reserved_types():
    em = EmulatedPanel()
    em.feed(P.hello_request(0))
    assert frames_of(em.feed(P.encode(0x55, 3)))[0].payload == bytes([3, P.NAK_BAD_TYPE])
    assert frames_of(em.feed(P.encode(P.T_OTA_BEGIN, 4)))[0].payload == bytes([4, P.NAK_UNSUPPORTED])


@pytest.mark.parametrize("seed", range(4))
def test_diff_rect_emulator_roundtrip_exact(seed):
    rnd = random.Random(seed)
    em = EmulatedPanel()
    seq = host_session(em)
    prev = bytes(P.FB_BYTES)
    for n_changes in (0, 1, 5, 200, 20000):
        new = mutate(rnd, prev, n_changes) if n_changes else prev
        rects, seq = send_diff(em, prev, new, seq)
        (ack,) = frames_of(em.feed(P.refresh(seq, full=False)))
        seq += 1
        assert ack.type == P.T_ACK
        assert bytes(em.glass) == new, f"n_changes={n_changes}"
        assert (not rects) == (n_changes == 0)
        prev = new


def test_full_refresh_and_random_frame():
    rnd = random.Random(99)
    em = EmulatedPanel()
    seq = host_session(em)
    new = rand_frame(rnd)
    _, seq = send_diff(em, bytes(P.FB_BYTES), new, seq)
    em.feed(P.refresh(seq, full=True))
    assert bytes(em.glass) == new
    assert em.refreshes[-1][0] == "F"


def test_partial_refresh_only_touches_dirty_bbox():
    em = EmulatedPanel()
    seq = host_session(em)
    em.feed(P.rect(seq, 16, 100, 16, 4, b"\xff" * 8))
    em.feed(P.refresh(seq + 1))
    assert em.refreshes[-1] == ("P", (16, 100, 16, 4), None)
    assert D.pixel(bytes(em.glass), 16, 100) == 1 and D.pixel(bytes(em.glass), 15, 100) == 0


def test_refresh_with_nothing_dirty_is_ack_noop():
    em = EmulatedPanel()
    seq = host_session(em)
    (f,) = frames_of(em.feed(P.refresh(seq)))
    assert f.type == P.T_ACK and em.refreshes == []


def test_png_written_on_refresh_and_matches(tmp_path):
    from PIL import Image

    em = EmulatedPanel(out_dir=str(tmp_path))
    seq = host_session(em)
    em.feed(P.rect(seq, 0, 0, 8, 1, b"\x80"))
    em.feed(P.refresh(seq + 1))
    path = em.refreshes[-1][2]
    assert path.endswith(".png")
    im = Image.open(path)
    assert im.size == (648, 480)
    assert im.getpixel((0, 0)) == 0 and im.getpixel((1, 0)) != 0  # black / white


def test_pbm_fallback(monkeypatch, tmp_path):
    import builtins

    real = builtins.__import__

    def no_pil(name, *a, **k):
        if name == "PIL" or name.startswith("PIL."):
            raise ImportError
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_pil)
    from inkterface_core.emulator import save_image

    p = save_image(bytes(P.FB_BYTES), str(tmp_path / "x.png"))
    assert p.endswith(".pbm")
    assert open(p, "rb").read().startswith(b"P4\n648 480\n")


def one(em, frame):
    (f,) = frames_of(em.feed(frame))
    return f


def test_mode_and_refresh_argument_errors():
    em = EmulatedPanel()
    host_session(em)
    for frame, reason in [
        (P.encode(P.T_MODE, 5, b""), P.NAK_BAD_LEN),
        (P.encode(P.T_MODE, 5, b"\x00\x00"), P.NAK_BAD_LEN),
        (P.encode(P.T_MODE, 5, b"\x02"), P.NAK_BAD_ARG),
        (P.encode(P.T_REFRESH, 5, b""), P.NAK_BAD_LEN),
        (P.encode(P.T_REFRESH, 5, b"\x00\x00"), P.NAK_BAD_LEN),
        (P.encode(P.T_REFRESH, 5, b"\x02"), P.NAK_BAD_ARG),
    ]:
        f = one(em, frame)
        assert (f.type, f.payload) == (P.T_NAK, bytes([5, reason])), frame.hex()
    assert em.mode == P.MODE_HOST and em.refreshes == []


def test_panel_to_host_types_are_ignored_and_session_starts_with_hello():
    em = EmulatedPanel()
    out = frames_of(em.feed(P.encode(P.T_LOG, 1, b"x")))
    assert [f.type for f in out] == [P.T_HELLO]  # first frame of a session
    assert em.feed(P.encode(P.T_ACK, 2, b"\x01") + P.encode(P.T_NAK, 3, b"\x01\x01")) == b""


def test_panel_tx_seq_increments_and_wraps():
    em = EmulatedPanel()
    em.feed(P.hello_request(0))
    seqs = [one(em, P.heartbeat(i)).seq for i in range(300)]
    assert seqs[:3] == [1, 2, 3] and seqs[254:257] == [255, 0, 1]


def test_hello_fw_string_truncated_to_64():
    em = EmulatedPanel(fw_ver="x" * 100)
    assert len(P.parse_hello(one(em, P.hello_request(0)).payload).fw_ver) == 64


def test_rect_accumulates_dirty_bbox_union():
    em = EmulatedPanel()
    seq = host_session(em)
    em.feed(P.rect(seq, 8, 10, 8, 2, b"\xff\xff"))
    em.feed(P.rect(seq + 1, 400, 300, 16, 3, b"\xff" * 6))
    em.feed(P.refresh(seq + 2))
    assert em.refreshes[-1][1] == (8, 10, 408, 293)


def test_full_frame_via_bands():
    em = EmulatedPanel()
    seq = host_session(em)
    new = bytes([0xF0]) * P.FB_BYTES
    for r in D.split_rect((0, 0, P.WIDTH, P.HEIGHT)):
        assert one(em, P.rect(seq, *r, D.crop(new, r))).type == P.T_ACK
        seq += 1
    assert bytes(em.fb) == new


def test_decoder_counters_and_flush_with_nothing_pending():
    d = P.Decoder()
    assert d.flush_stale() == []
    d.feed(b"abc" + P.heartbeat(1))
    assert d.skipped == 3 and d.crc_errors == 0 and d.bad_len == 0


# -- IDLE_LAYOUT (mirrors idle::Uploader in firmware/src/ee05/idle_store.cpp) ---------------------------------------
def _store():
    from inkterface_core.idle.compile import compile_layout
    from inkterface_core.idle.store import pack_store
    return pack_store([compile_layout({"render_mode": "mono", "widgets": [{"type": "text", "x": 0, "y": 0, "w": 100, "h": 20, "text": "hi"}]})])


def _idle(em, seq, total, off, data):
    return [(f.type, f.payload) for f in frames_of(em.feed(P.idle_layout(seq, total, off, data)))]


def _idle_panel():
    em = EmulatedPanel(caps=P.CAP_GRAY4 | P.CAP_IDLE_STORAGE, idle_free=49152)
    em.feed(P.heartbeat(0))
    return em


def test_idle_layout_unsupported_without_the_capability():
    em = EmulatedPanel()
    em.feed(P.heartbeat(0))
    assert _idle(em, 1, 10, 0, b"x" * 10) == [(P.T_NAK, bytes([1, P.NAK_UNSUPPORTED]))]


def test_idle_layout_chunks_commit_on_the_last_one():
    em, blob = _idle_panel(), _store()
    half = len(blob) // 2
    assert _idle(em, 1, len(blob), 0, blob[:half]) == [(P.T_ACK, b"\x01")]
    assert em.idle_store is None
    assert _idle(em, 2, len(blob), half, blob[half:]) == [(P.T_ACK, b"\x02")]
    assert em.idle_store == blob and em.idle_commits == 1
    assert _idle(em, 3, len(blob), half, blob[half:]) == [(P.T_ACK, b"\x03")]   # lost-ACK resend of the final chunk
    assert em.idle_commits == 1


def test_idle_layout_refusals():
    em, blob = _idle_panel(), _store()
    nak = lambda seq, r: [(P.T_NAK, bytes([seq, r]))]   # noqa: E731
    assert _idle(em, 1, 0, 0, b"x") == nak(1, P.NAK_BAD_LEN)                      # total 0
    assert _idle(em, 2, 49153, 0, b"x") == nak(2, P.NAK_BAD_LEN)                  # total over the store
    assert _idle(em, 3, 10, 5, b"x" * 10) == nak(3, P.NAK_BAD_LEN)                # offset + len > total
    assert _idle(em, 4, len(blob), 5, blob[5:]) == nak(4, P.NAK_BAD_ARG)          # no upload started
    assert _idle(em, 5, len(blob), 0, blob[:20]) == [(P.T_ACK, b"\x05")]
    assert _idle(em, 6, len(blob), 30, blob[30:40]) == nak(6, P.NAK_BAD_ARG)      # gap
    assert _idle(em, 7, len(blob) + 1, 20, blob[20:30]) == nak(7, P.NAK_BAD_ARG)  # total changed
    assert _idle(em, 8, len(blob), 0, blob[:20]) == [(P.T_ACK, b"\x08")]          # offset 0 restarts
    assert _idle(em, 9, len(blob), 0, b"\xff" * 20) == [(P.T_ACK, b"\x09")]
    assert _idle(em, 10, len(blob), 20, blob[20:]) == nak(10, P.NAK_BAD_DATA)     # store fails validation (crc)
    assert em.idle_store is None
    assert frames_of(em.feed(P.encode(P.T_IDLE_LAYOUT, 11, b"short"))) [0].payload == bytes([11, P.NAK_BAD_LEN])


def test_idle_layout_nak_knob_and_state_reset():
    em, blob = _idle_panel(), _store()
    em.idle_nak_with = P.NAK_STORAGE
    assert _idle(em, 1, len(blob), 0, blob) == [(P.T_NAK, bytes([1, P.NAK_STORAGE]))]
    assert em.idle_store is None
    assert _idle(em, 2, len(blob), 0, blob) == [(P.T_ACK, b"\x02")] and em.idle_store == blob   # knob is one-shot
