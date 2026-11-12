"""Upload an ISTR store to the panel over IDLE_LAYOUT, and set MODE.

docs/idle-mode.md "Transport": each chunk is `total u32 | offset u32 | data`, sent in
order; a lost ACK is handled by resending the same chunk (the panel ACKs a repeat with no
effect). The final chunk's ACK means validated and durably written, so it gets a longer
timeout. A NAK raises with the reason name.
"""
from __future__ import annotations

import time

from .. import proto as P
from . import fields as F


class UploadError(RuntimeError):
    def __init__(self, reason: int | str, msg: str) -> None:
        self.reason = reason
        super().__init__(msg)


def _wait_reply(link, seq: int, timeout: float):
    """Wait for an ACK or NAK carrying `seq`. Returns ('ack'|'nak', reason)."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        for f in link.poll(0.05):
            if f.type == P.T_ACK and f.payload and f.payload[0] == seq:
                return "ack", 0
            if f.type == P.T_NAK and len(f.payload) >= 2 and f.payload[0] == seq:
                return "nak", f.payload[1]
    return "timeout", 0


def upload_store(link, blob: bytes, *, chunk: int = 4000, retries: int = 3,
                 timeout: float = 1.0, final_timeout: float = 3.0) -> None:
    """Send `blob` as IDLE_LAYOUT chunks. Raises UploadError on NAK or exhausted retries."""
    blob = bytes(blob)
    total = len(blob)
    if total == 0:
        raise UploadError("BAD_DATA", "empty store")
    if total > F.MAX_STORE_BYTES:
        raise UploadError("BAD_DATA", f"store {total} > {F.MAX_STORE_BYTES}")
    if chunk < 1 or chunk > P.MAX_PAYLOAD - 8:
        raise UploadError("BAD_LEN", f"chunk {chunk} out of 1..{P.MAX_PAYLOAD - 8}")

    offset = 0
    while offset < total:
        data = blob[offset : offset + chunk]
        is_final = offset + len(data) >= total
        seq = link.next_seq()
        frame = P.idle_layout(seq, total, offset, data)
        to = final_timeout if is_final else timeout
        last_reason = None
        for _ in range(retries):
            link.send(frame)
            kind, reason = _wait_reply(link, seq, to)
            if kind == "ack":
                break
            if kind == "nak":
                name = P.NAK_NAMES.get(reason, str(reason))
                raise UploadError(reason, f"upload NAK at offset {offset}: {name}")
            last_reason = "timeout"
        else:
            raise UploadError("timeout", f"no ACK for chunk at offset {offset} ({last_reason})")
        offset += len(data)


def set_mode(link, mode: int, *, idle_timeout_s: int = 0, layout: int = 0xFF,
             timeout: float = 5.0, retries: int = 3) -> None:
    """Send MODE with optional idle_timeout_s (0 keep, else 15..3600) and layout (0xFF keep).

    timeout 5 s: a panel that is idle may be busy with a full e-paper refresh (up to ~4.1 s) when the
    frame arrives, e.g. right after a layout upload; the frame waits in its RX queue."""
    payload_frame = None
    for _ in range(retries):
        seq = link.next_seq()
        payload_frame = P.mode_ex(seq, mode, idle_timeout_s, layout)
        link.send(payload_frame)
        kind, reason = _wait_reply(link, seq, timeout)
        if kind == "ack":
            return
        if kind == "nak":
            name = P.NAK_NAMES.get(reason, str(reason))
            raise UploadError(reason, f"MODE NAK: {name}")
    raise UploadError("timeout", "no ACK for MODE")
