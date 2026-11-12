"""Streamer against the emulator: success, NAKs, dropped link + resume, cancel, lost frames."""
import hashlib

import pytest
from ota_helpers import SLOT, Bench, FakeSerial, make_image

from inkterface_core import proto as P
from inkterface_core.emulator import EmulatedPanel
from inkterface_core.ota import errors as E
from inkterface_core.ota import stream
from inkterface_core.usb_link import Link

IMG = make_image(60_000)  # 15 DATA frames
VER = "1.2.0"
FAST = dict(timeout=0.3, begin_timeout=1.0, end_timeout=1.0, ack_timeout=0.2, sleep=lambda s: None)
STREAM = dict(timeout=0.3, begin_timeout=1.0, end_timeout=1.0)


def run(bench, image=IMG, **kw):
    return stream.install(bench.open_panel, image, VER, **{**FAST, **kw})


def test_install_success_confirms_new_image():
    b = Bench()
    seen = []
    hello = run(b, progress=lambda done, total: seen.append((done, total)))
    p = b.panel
    assert hello.fw_ver == VER and p.fw_ver == VER and p.prev_fw_ver == "1.0.0"
    assert p.installed == IMG
    assert p.pending_verify is False  # the HELLO ack marked it valid
    assert seen[0] == (0, len(IMG)) and seen[-1] == (len(IMG), len(IMG))
    assert [d for d, _ in seen] == sorted(d for d, _ in seen)
    assert all(s.closed for s in b.opens)
    assert b.panel.reboots == 1


def test_window_never_exceeds_rx_window():
    b = Bench()
    link, hello = b.open_panel()
    inflight_max = 0
    orig_send = link.send

    def spy(data):
        nonlocal inflight_max
        orig_send(data)
        # unacked = wire bytes written but not yet answered; the fake answers synchronously, so
        # measure what the streamer believed: sum of wire lengths of frames sent since last poll
        spy.n += len(data)
        inflight_max = max(inflight_max, spy.n)

    spy.n = 0
    orig_poll = link.poll

    def poll(t):
        spy.n = 0
        return orig_poll(t)

    link.send, link.poll = spy, poll
    stream.stream_image(link, hello, IMG, VER, **STREAM)
    assert 0 < inflight_max <= hello.rx_window


def test_unsupported_firmware():
    b = Bench(EmulatedPanel(fw_ver="old"))  # no ota_slot: no CAP_OTA, NAKs OTA_* UNSUPPORTED
    link, hello = b.open_panel()
    assert not hello.caps & P.CAP_OTA
    with pytest.raises(E.OtaUnsupported):
        stream.stream_image(link, hello, IMG, VER)


def test_panel_naks_begin_too_big():
    b = Bench(EmulatedPanel(fw_ver="1.0.0", ota_slot=1000))
    with pytest.raises(E.OtaRejected) as e:
        run(b)
    assert e.value.name == "OTA_TOO_BIG" and e.value.reason == P.NAK_OTA_TOO_BIG


def test_nak_mid_stream_flash_failure_is_not_retried():
    b = Bench()
    b.panel.ota_fail_write_at = 30_000
    with pytest.raises(E.OtaRejected) as e:
        run(b)
    assert e.value.name == "OTA_FLASH_FAIL"
    assert 0 < e.value.offset <= 30_000 < e.value.offset + P.OTA_MAX_CHUNK
    assert len(b.opens) == 1  # no retry loop for a real rejection
    assert b.panel.fw_ver == "1.0.0" and b.panel.installed is None  # nothing was switched


def test_hash_mismatch_at_end_and_clean_retry_after():
    b = Bench()
    b.panel.ota_corrupt_at = 12_345
    with pytest.raises(E.OtaRejected) as e:
        run(b)
    assert e.value.name == "OTA_HASH_MISMATCH"
    assert b.panel.fw_ver == "1.0.0" and b.panel.installed is None
    b.panel.ota_corrupt_at = None
    run(b)  # session was dropped by the panel: this is a fresh full transfer
    assert b.panel.fw_ver == VER


def test_bad_image_rejected_at_end():
    bad = b"\x00" + IMG[1:]  # no 0xE9 magic
    b = Bench()
    with pytest.raises(E.OtaRejected) as e:
        run(b, image=bad)
    assert e.value.name == "OTA_BAD_IMAGE"


def test_link_drop_mid_stream_resumes_from_panel_offset():
    b = Bench()
    b.next_open(drop_after=25_000)  # first connection dies after ~6 frames, mid-frame
    firsts = []
    run(b, progress=lambda d, t: firsts.append(d))
    assert b.panel.fw_ver == VER and b.panel.installed == IMG
    assert len(b.opens) >= 3  # first (dropped), second (resume), then the post-reboot confirm link
    resume_from = [d for d in firsts if 0 < d < len(IMG)][0]
    assert resume_from > 0
    # second connection must not resend what the panel already held
    sent_second = b.opens[1].written
    assert sent_second < len(IMG) - resume_from + 2000
    assert b.opens[0].written + sent_second < len(IMG) * 1.1 + 2000


def test_link_drop_during_end_wait_resumes_and_finishes():
    b = Bench()
    # everything delivered, then the link dies right after the last DATA frame
    b.next_open(drop_after=len(IMG) + 15 * 14 + 46 + 6)  # BEGIN (46) + 15 DATA frames, then END is cut
    run(b)
    assert b.panel.fw_ver == VER


def test_retries_exhausted():
    b = Bench()
    b.handshake_timeout = 0.1
    for _ in range(3):
        b.next_open(mute=True)
    with pytest.raises(E.LinkLost):
        run(b, retries=2, begin_timeout=0.1)
    assert len(b.opens) == 3 and b.panel.fw_ver == "1.0.0"


def test_open_panel_failures_are_retried_then_raised():
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise OSError("no such device")
        return b.open_panel()

    b = Bench()
    stream.install(flaky, IMG, VER, **FAST)
    assert len(calls) >= 3 and b.panel.fw_ver == VER

    def never():
        raise OSError("gone")

    with pytest.raises(OSError):
        stream.install(never, IMG, VER, retries=1, **FAST)


def test_cancel_then_resume_later():
    b = Bench()
    state = {"n": 0}

    def progress(done, total):
        state["n"] += 1

    with pytest.raises(E.Cancelled):
        run(b, progress=progress, cancel=lambda: state["n"] >= 4)
    held = len(b.panel._ota.data)
    assert 0 < held < len(IMG)  # panel kept the partial session
    assert b.panel.installed is None and b.panel.fw_ver == "1.0.0"
    starts = []
    run(b, progress=lambda d, t: starts.append(d))
    assert starts[0] == held  # resumed, not restarted
    assert b.panel.fw_ver == VER


def test_cancel_before_anything_is_sent_after_begin_reply():
    b = Bench()
    with pytest.raises(E.Cancelled):
        run(b, cancel=lambda: True)
    assert b.panel._ota is None or len(b.panel._ota.data) == 0


def test_lost_frame_gives_bad_offset_then_resync_and_success():
    b = Bench()
    b.next_open(lose_write=[5])  # BEGIN is write 0; the 5th DATA frame never reaches the panel
    run(b)
    assert b.panel.fw_ver == VER and b.panel.installed == IMG
    assert len(b.opens) >= 3  # first attempt resynced onto a new connection


def test_no_ack_timeout_is_link_lost():
    b = Bench()
    link, hello = b.open_panel()
    link.ser.mute = True
    with pytest.raises(E.LinkLost):
        stream.stream_image(link, hello, IMG, VER, begin_timeout=0.1)
    link2, hello2 = b.open_panel()
    # BEGIN answered, then the panel goes silent for DATA
    orig = link2.ser._deliver

    def deliver(data):
        orig(data)
        link2.ser.mute = True

    link2.ser._deliver = deliver
    with pytest.raises(E.LinkLost, match="no ACK"):
        stream.stream_image(link2, hello2, IMG, VER, timeout=0.1)


def test_serial_errors_become_link_lost():
    b = Bench()
    link, hello = b.open_panel()
    link.ser.dead = True
    with pytest.raises(E.LinkLost):
        stream.stream_image(link, hello, IMG, VER)
    with pytest.raises(E.LinkLost):
        stream.handshake(link, timeout=0.2)


def test_panel_reports_more_bytes_than_image():
    b = Bench()
    link, hello = b.open_panel()
    big = make_image(10_000)
    # open a panel session for the same params but claim the image is smaller than the panel holds
    link.send(P.ota_begin(link.next_seq(), len(big), hashlib.sha256(big).digest(), VER))
    link.poll(0.05)
    b.panel._ota.data = bytearray(big) + b"extra"  # impossible state; guard must trip
    with pytest.raises(E.OtaResync):
        stream.stream_image(link, hello, big, VER, **STREAM)


def test_end_reports_incomplete_as_resync():
    b = Bench()
    link, hello = b.open_panel()
    orig_poll = link.poll

    def poll(t):
        fr = orig_poll(t)
        if b.panel._ota and len(b.panel._ota.data) == len(IMG):
            del b.panel._ota.data[-10:]  # panel "forgets" the tail after ACKing it
        return fr

    link.poll = poll
    with pytest.raises(E.OtaResync):
        stream.stream_image(link, hello, IMG, VER, **STREAM)


# --- confirm_boot ------------------------------------------------------------------------
class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def test_confirm_boot_wrong_version_never_acks_and_watchdog_rolls_back():
    b = Bench()
    stream.stream_image(*b.open_panel(), IMG, VER, **STREAM)
    clk = Clock()
    # the panel boots something else than the plugin expects: do not ack
    b.panel.ota_committed.version = b"9.9.9"
    with pytest.raises(E.BootMismatch, match="expected"):
        stream.confirm_boot(b.open_panel, VER, timeout=5, sleep=clk.sleep, clock=clk)
    assert b.panel.fw_ver == "9.9.9" and b.panel.pending_verify is True
    b.panel.watchdog_expire()  # verify timer fires: bootloader rolls back
    assert b.panel.fw_ver == "1.0.0" and b.panel.pending_verify is False


def test_confirm_boot_panel_never_returns():
    clk = Clock()

    def gone():
        raise OSError("no device")

    with pytest.raises(E.BootMismatch, match="no device"):
        stream.confirm_boot(gone, VER, timeout=10, sleep=clk.sleep, clock=clk)
    assert clk.t >= 10


def test_confirm_boot_waits_for_slow_reboot_then_acks():
    b = Bench()
    stream.stream_image(*b.open_panel(), IMG, VER, **STREAM)
    clk = Clock()
    calls = {"n": 0}

    def slow():
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("re-enumerating")
        if calls["n"] == 2:  # reconnected to the OLD image before it reset
            return Link(FakeSerial(EmulatedPanel(fw_ver="1.0.0", ota_slot=SLOT))), P.Hello(1, 4096, 8192, 648, 480, 4, 0, "1.0.0")
        return b.open_panel()

    hello = stream.confirm_boot(slow, VER, timeout=30, sleep=clk.sleep, clock=clk)
    assert hello.fw_ver == VER and b.panel.pending_verify is False and calls["n"] == 3


def test_confirm_boot_no_hello_reply_to_ack_is_retried():
    b = Bench()
    stream.stream_image(*b.open_panel(), IMG, VER, **STREAM)
    clk = Clock()
    n = {"i": 0}

    def open_():
        n["i"] += 1
        link, hello = b.open_panel()
        if n["i"] == 1:  # panel goes mute right after the handshake: the ack gets no echo
            link.ser.mute = True
        return link, hello

    # the first ack still reaches the panel (so it validates), but the host cannot know: it retries
    hello = stream.confirm_boot(open_, VER, timeout=60, ack_timeout=0.2, sleep=clk.sleep, clock=clk)
    assert hello.fw_ver == VER and n["i"] == 2 and b.panel.pending_verify is False


def test_confirm_boot_rejects_protocol_mismatch():
    clk = Clock()
    link = Link(FakeSerial(EmulatedPanel(fw_ver=VER, ota_slot=SLOT)))
    hello = P.Hello(P.PROTO_VER + 1, 4096, 8192, 648, 480, 4, 0, VER)
    with pytest.raises(E.BootMismatch, match="protocol"):
        stream.confirm_boot(lambda: (link, hello), VER, timeout=3, sleep=clk.sleep, clock=clk)
    assert link.ser.written == 0  # nothing sent: no ack for a protocol we do not speak


def test_handshake_timeout():
    link = Link(FakeSerial(EmulatedPanel(), mute=True))
    with pytest.raises(E.LinkLost, match="HELLO"):
        stream.handshake(link, timeout=0.1)


def test_stream_failures_exhaust_retries_after_handshake():
    b = Bench()
    for _ in range(2):
        b.next_open(mute_after=1)  # handshake works, BEGIN is never answered
    with pytest.raises(E.LinkLost, match="did not answer"):
        run(b, retries=1, begin_timeout=0.1)
    assert len(b.opens) == 2


def test_poll_oserror_and_stray_frames_and_close_errors():
    b = Bench()
    link, hello = b.open_panel()
    # unrelated frames on the wire are ignored while waiting for our ACK
    link.ser.rx += P.encode(P.T_HELLO, 9, P.hello_payload(hello)) + P.encode(P.T_ACK, 200, b"\x05") + P.encode(P.T_NAK, 201, b"\x05\x01")
    stream.stream_image(link, hello, IMG, VER, **STREAM)
    link2, _ = b.open_panel()
    link2.ser.dead = True
    with pytest.raises(E.LinkLost):
        stream._poll(link2, 0.01)

    def boom():
        raise OSError("close failed")

    link2.ser.close = boom
    stream._close(link2)  # swallowed
    stream._close(Link(object()))  # no close(): fine


def test_confirm_boot_send_failure_is_retried():
    b = Bench()
    stream.stream_image(*b.open_panel(), IMG, VER, **STREAM)
    clk = Clock()
    n = {"i": 0}

    def open_():
        n["i"] += 1
        link, hello = b.open_panel()
        if n["i"] == 1:
            link.ser.dead = True  # dies right after the handshake: the ack cannot be written
        return link, hello

    hello = stream.confirm_boot(open_, VER, timeout=30, ack_timeout=0.2, sleep=clk.sleep, clock=clk)
    assert hello.fw_ver == VER and n["i"] == 2
