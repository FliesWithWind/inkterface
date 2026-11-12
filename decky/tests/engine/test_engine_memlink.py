import errno

import pytest

from engine_testlib import Clock
from inkterface_core import proto as P
from inkterface_core.engine import EmulatedLink


def types(frames):
    return [f.type for f in frames]


def test_hello_roundtrip_and_ack():
    link = EmulatedLink(clock=Clock())
    link.write(P.hello_request(0))
    link.write(P.mode(1, P.MODE_HOST))
    dec = P.Decoder()
    got = dec.feed(link.read(4096, 0))
    assert types(got) == [P.T_HELLO, P.T_ACK]
    assert P.parse_hello(got[0].payload).width == 648 and got[1].payload == b"\x01"
    assert link.read(10, 0) == b""


def test_read_respects_size():
    link = EmulatedLink(clock=Clock())
    link.write(P.hello_request(0))
    first = link.read(5, 0)
    assert len(first) == 5
    rest = link.read(4096, 0)
    assert P.Decoder().feed(first + rest)[0].type == P.T_HELLO


def test_refresh_makes_the_panel_deaf_and_delays_its_ack():
    c = Clock(10.0)
    link = EmulatedLink(clock=c, partial_s=1.8, full_s=4.1)
    link.write(P.mode(0, P.MODE_HOST))
    link.read(4096, 0)
    link.write(P.refresh(1, False))
    assert link.read(4096, 0) == b""  # not done yet
    link.write(P.heartbeat(2))  # panel is blocked: queued, not answered
    c.t = 11.7
    assert link.read(4096, 0) == b""
    assert link.max_pending_refresh == 1
    c.t = 11.8
    got = P.Decoder().feed(link.read(4096, 0))
    assert [(f.type, f.payload[0]) for f in got] == [(P.T_ACK, 1), (P.T_ACK, 2)]
    link.write(P.refresh(3, True))
    c.t = 15.8
    assert link.read(4096, 0) == b""
    c.t = 15.9
    assert link.read(4096, 0)


def test_pending_refresh_counter_sees_a_queued_second_refresh():
    c = Clock(0.0)
    link = EmulatedLink(clock=c, partial_s=2.0)
    link.write(P.mode(0, P.MODE_HOST))
    link.write(P.refresh(1, False))
    link.write(P.refresh(2, False))
    assert link.max_pending_refresh == 2


def test_backlog_is_tracked():
    c = Clock(0.0)
    link = EmulatedLink(clock=c, partial_s=2.0)
    link.write(P.mode(0, P.MODE_HOST))
    link.write(P.refresh(1, False))
    data = P.heartbeat(2) * 50
    link.write(data)
    assert link.max_backlog >= len(data)


def test_unplug_raises_eio():
    link = EmulatedLink(clock=Clock())
    link.unplug()
    for call in (lambda: link.read(1, 0), lambda: link.write(b"x")):
        with pytest.raises(OSError) as e:
            call()
        assert e.value.errno == errno.EIO


def test_unplug_mid_write_delivers_a_prefix_only():
    link = EmulatedLink(clock=Clock())
    link.fail_after = 5
    wire = P.hello_request(0)
    with pytest.raises(OSError):
        link.write(wire)
    assert link.written == []  # half a frame is not a frame
    with pytest.raises(OSError):
        link.read(1, 0)


def test_fail_after_counts_across_writes():
    link = EmulatedLink(clock=Clock())
    link.fail_after = 15
    link.write(P.hello_request(0))  # 10 bytes
    with pytest.raises(OSError):
        link.write(P.hello_request(1))


def test_closed_link_raises_value_error():
    link = EmulatedLink(clock=Clock())
    link.close()
    with pytest.raises(ValueError):
        link.read(1, 0)


def test_mute_drops_replies():
    link = EmulatedLink(clock=Clock())
    link.mute = True
    link.write(P.hello_request(0))
    assert link.read(4096, 0) == b"" and len(link.written) == 1


def test_new_link_discards_a_half_frame_left_in_the_panel():
    from inkterface_core.emulator import EmulatedPanel
    panel = EmulatedPanel()
    first = EmulatedLink(panel, Clock())
    first.fail_after = 3000
    with pytest.raises(OSError):
        first.write(P.rect(0, 0, 0, 648, 6, bytes(486 * 8))[:4000] if False else P.rect(0, 0, 0, 648, 50, bytes(50 * 81)))
    second = EmulatedLink(panel, Clock())  # fresh port: panel flushes the stale partial frame
    second.write(P.hello_request(1))
    assert P.Decoder().feed(second.read(4096, 0))[0].type == P.T_HELLO
