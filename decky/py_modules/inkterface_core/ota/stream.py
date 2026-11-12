"""Stream a firmware image to the panel over USB (OTA_BEGIN / OTA_DATA / OTA_END).

Wire rules: docs/usb-protocol-v1.md "OTA". The panel keeps its transfer session across a
dropped link, and the ACK to OTA_BEGIN says how many bytes it already holds, so a retry
resumes instead of restarting. `install` is the one call the plugin makes:

    install(open_panel, image, version) -> stream (resume on LinkLost/OtaResync) -> confirm_boot

`open_panel() -> (usb_link.Link, proto.Hello)` opens the port and does the HELLO handshake
(`handshake`); it raises OSError / Retryable when the panel is not there (yet).
"""
from __future__ import annotations

import hashlib
import time
from typing import Callable

from .. import proto as P
from .errors import BootMismatch, Cancelled, LinkLost, OtaRejected, OtaResync, OtaUnsupported, Retryable

CHUNK = 4080  # two DATA frames (4094 wire bytes each) fit in the panel's 8192-byte rx window
_FRAME_OVERHEAD = P.HDR_LEN + P.CRC_LEN + 4  # + offset u32

Progress = Callable[[int, int], None]  # (bytes confirmed by the panel, total)


def _check(cancel) -> None:
    if cancel is not None and cancel():
        raise Cancelled("update cancelled")


def _poll(link, timeout: float):
    try:
        return link.poll(timeout)
    except OSError as e:
        raise LinkLost(f"serial error: {e}") from None


def _send(link, data: bytes) -> None:
    try:
        link.send(data)
    except OSError as e:
        raise LinkLost(f"serial error: {e}") from None


def _reply(link, seq: int, timeout: float, cancel):
    """Wait for the ACK/NAK of host frame `seq`. Anything else on the wire is ignored."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        _check(cancel)
        for f in _poll(link, 0.02):
            if f.type in (P.T_ACK, P.T_NAK) and f.payload and f.payload[0] == seq:
                return f
    raise LinkLost("panel did not answer in time")


def _rejected(f, offset=None) -> OtaRejected:
    reason = f.payload[1] if len(f.payload) > 1 else 0
    return OtaRejected(reason, P.NAK_NAMES.get(reason, str(reason)), offset)


def handshake(link, timeout: float = 30.0) -> P.Hello:
    """HELLO request until the panel answers. A freshly reset panel can need ~30 s (docs)."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        _send(link, P.hello_request(link.next_seq()))
        for f in _poll(link, 1.0):
            if f.type == P.T_HELLO:
                return P.parse_hello(f.payload)
    raise LinkLost("no HELLO from panel")


def stream_image(
    link,
    hello: P.Hello,
    image: bytes,
    version: str,
    *,
    progress: Progress | None = None,
    cancel: Callable[[], bool] | None = None,
    timeout: float = 5.0,
    begin_timeout: float = 30.0,
    end_timeout: float = 60.0,
    chunk: int = CHUNK,
) -> None:
    """One streaming attempt, resuming at whatever offset the panel reports.

    Raises OtaUnsupported, OtaRejected (panel NAK, not retryable), Cancelled, or a Retryable
    (LinkLost: no ACK within `timeout` / serial error; OtaResync: panel's offset != ours).
    On cancel nothing is sent: the panel keeps the partial session until it reboots or gets a
    different OTA_BEGIN, so the same image can be resumed later.
    """
    if not hello.caps & P.CAP_OTA:
        raise OtaUnsupported("this firmware has no OTA support; flash it once over USB with pio")
    size, sha = len(image), hashlib.sha256(image).digest()
    chunk = max(1, min(chunk, P.OTA_MAX_CHUNK, hello.max_payload - 4))

    seq = link.next_seq()
    _send(link, P.ota_begin(seq, size, sha, version))
    f = _reply(link, seq, begin_timeout, cancel)
    if f.type == P.T_NAK:
        raise _rejected(f)
    confirmed = next_off = P.parse_ack_received(f.payload) or 0
    if confirmed > size:
        raise OtaResync("panel reports more bytes than the image has")
    if progress:
        progress(confirmed, size)

    pending: dict[int, tuple[int, int]] = {}  # seq -> (offset of its first byte, wire length)
    deadline = time.monotonic() + timeout
    while confirmed < size:
        _check(cancel)
        while next_off < size:  # keep <= rx_window unacknowledged wire bytes in flight
            n = min(chunk, size - next_off)
            wire = n + _FRAME_OVERHEAD
            if pending and sum(w for _, w in pending.values()) + wire > hello.rx_window:
                break
            seq = link.next_seq()
            _send(link, P.ota_data(seq, next_off, image[next_off : next_off + n]))
            pending[seq] = (next_off, wire)
            next_off += n
        for f in _poll(link, 0.02):
            if f.type not in (P.T_ACK, P.T_NAK) or not f.payload or f.payload[0] not in pending:
                continue
            off, _ = pending.pop(f.payload[0])
            if f.type == P.T_NAK:
                if len(f.payload) > 1 and f.payload[1] == P.NAK_OTA_BAD_OFFSET:
                    raise OtaResync(f"panel expected a different offset than {off}")
                raise _rejected(f, off)
            got = P.parse_ack_received(f.payload)
            if got is not None and got > confirmed:
                confirmed = got
                if progress:
                    progress(confirmed, size)
            deadline = time.monotonic() + timeout
        if time.monotonic() > deadline:
            raise LinkLost(f"no ACK for {timeout:.0f} s at offset {confirmed}")

    seq = link.next_seq()
    _send(link, P.ota_end(seq))
    f = _reply(link, seq, end_timeout, cancel)
    if f.type == P.T_NAK:
        if len(f.payload) > 1 and f.payload[1] == P.NAK_OTA_BAD_OFFSET:
            raise OtaResync("panel says the transfer is incomplete")
        raise _rejected(f)


def _close(link) -> None:
    close = getattr(getattr(link, "ser", None), "close", None)
    if close:
        try:
            close()
        except OSError:
            pass


def confirm_boot(
    open_panel,
    version: str,
    *,
    timeout: float = 120.0,
    ack_timeout: float = 5.0,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> P.Hello:
    """After OTA_END: reconnect, check the panel runs `version`, then send the HELLO-ack.

    The ack is what makes the panel mark the new image valid. Without it the panel's verify
    timer expires and the bootloader rolls back, so do NOT ack (or send any other frame
    that follows a HELLO) for a version / protocol you do not accept.
    """
    end = clock() + timeout
    seen = "no answer from the panel"
    while clock() < end:
        try:
            link, hello = open_panel()
        except (OSError, Retryable) as e:
            seen = str(e) or type(e).__name__
            sleep(1.0)
            continue
        try:
            if hello.fw_ver == version and hello.proto_ver == P.PROTO_VER:
                seq = link.next_seq()
                _send(link, P.hello_ack(seq, hello))
                if any(f.type == P.T_HELLO for f in _poll(link, ack_timeout)):  # panel echoes HELLO
                    return hello
                seen = "panel did not answer the HELLO ack"
            else:
                seen = f"panel runs {hello.fw_ver!r} (protocol {hello.proto_ver}), expected {version!r}"
        except Retryable as e:
            seen = str(e)
        finally:
            _close(link)
        sleep(1.0)
    raise BootMismatch(f"panel did not come back on {version}: {seen}")


def install(
    open_panel,
    image: bytes,
    version: str,
    *,
    progress: Progress | None = None,
    cancel: Callable[[], bool] | None = None,
    retries: int = 3,
    confirm_timeout: float = 120.0,
    ack_timeout: float = 5.0,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    **stream_kw,
) -> P.Hello:
    """Stream `image` (resuming after dropped links), reboot, confirm. Returns the new HELLO."""
    for attempt in range(retries + 1):
        try:
            link, hello = open_panel()
        except (OSError, Retryable):
            if attempt == retries:
                raise
            sleep(1.0)
            continue
        try:
            stream_image(link, hello, image, version, progress=progress, cancel=cancel, **stream_kw)
            break
        except Retryable:
            if attempt == retries:
                raise
        finally:
            _close(link)
    return confirm_boot(open_panel, version, timeout=confirm_timeout, ack_timeout=ack_timeout, sleep=sleep, clock=clock)
