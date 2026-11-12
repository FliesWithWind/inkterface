"""upload_store / set_mode against a fake link: happy path, NAK, retry, final-chunk timeout.
Plus the proto.py idle helpers (IDLE_LAYOUT chunk, MODE with idle_timeout_s 0 or 15..3600)."""
import struct

import pytest

from inkterface_core import proto as P
from inkterface_core.idle import fields as F
from inkterface_core.idle.upload import upload_store, set_mode, UploadError


class FakeLink:
    """Records chunks, replies with scripted ACK/NAK/timeout per received offset."""

    def __init__(self, behaviour=None):
        self.seq = 0
        self.sent = []          # (offset, len, is_final)
        self._pending = []      # frames to hand back on next poll
        self.behaviour = behaviour or {}  # offset -> list of 'ack'|'nak:R'|'timeout'
        self._counts = {}

    def next_seq(self):
        s = self.seq
        self.seq = (self.seq + 1) & 0xFF
        return s

    def send(self, data):
        dec = P.Decoder()
        (f,) = dec.feed(data)
        if f.type == P.T_IDLE_LAYOUT:
            total, offset = struct.unpack_from("<II", f.payload)
            dlen = len(f.payload) - 8
            self.sent.append((offset, dlen, offset + dlen >= total))
            self._schedule(offset, f.seq)
        elif f.type == P.T_MODE:
            self.sent.append(("MODE", f.payload, False))
            self._schedule("MODE", f.seq)

    def _schedule(self, key, seq):
        script = self.behaviour.get(key, ["ack"])
        i = self._counts.get(key, 0)
        action = script[min(i, len(script) - 1)]
        self._counts[key] = i + 1
        if action == "ack":
            self._pending.append(P.Frame(P.T_ACK, 0, bytes([seq])))
        elif action.startswith("nak"):
            reason = int(action.split(":")[1])
            self._pending.append(P.Frame(P.T_NAK, 0, bytes([seq, reason])))
        # 'timeout' -> schedule nothing

    def poll(self, timeout=0.0):
        out, self._pending = self._pending, []
        return out


def test_upload_happy_path_chunks_in_order():
    blob = bytes(range(256)) * 40  # 10240 bytes
    link = FakeLink()
    upload_store(link, blob, chunk=4000)
    offsets = [s[0] for s in link.sent]
    assert offsets == [0, 4000, 8000]
    assert link.sent[-1][2] is True  # final flagged
    # reassembled length matches
    assert sum(s[1] for s in link.sent) == len(blob)


def test_upload_nak_raises_reason_name():
    blob = b"x" * 100
    link = FakeLink(behaviour={0: ["nak:14"]})
    with pytest.raises(UploadError) as e:
        upload_store(link, blob)
    assert "BAD_DATA" in str(e.value) and e.value.reason == 14


def test_upload_storage_nak():
    link = FakeLink(behaviour={0: ["nak:15"]})
    with pytest.raises(UploadError, match="STORAGE"):
        upload_store(link, b"y" * 50)


def test_upload_retries_then_succeeds():
    # first send of offset 0 times out, retry ACKs
    link = FakeLink(behaviour={0: ["timeout", "ack"]})
    upload_store(link, b"z" * 20, timeout=0.05, final_timeout=0.05, retries=3)
    # offset 0 was sent twice
    assert sum(1 for s in link.sent if s[0] == 0) == 2


def test_upload_final_chunk_timeout_raises():
    blob = b"w" * 20
    link = FakeLink(behaviour={0: ["timeout", "timeout", "timeout"]})
    with pytest.raises(UploadError, match="no ACK"):
        upload_store(link, blob, timeout=0.02, final_timeout=0.02, retries=3)


def test_set_mode_sends_payload_and_acks():
    link = FakeLink(behaviour={"MODE": ["ack"]})
    set_mode(link, P.MODE_PANEL, idle_timeout_s=60, layout=2, timeout=0.1)
    key, payload, _ = link.sent[0]
    assert key == "MODE"
    m, t, lay = struct.unpack("<BHB", payload)
    assert m == P.MODE_PANEL and t == 60 and lay == 2


def test_set_mode_nak():
    link = FakeLink(behaviour={"MODE": ["nak:4"]})
    with pytest.raises(UploadError, match="BAD_ARG"):
        set_mode(link, P.MODE_PANEL, timeout=0.05)


# --- proto helpers ----------------------------------------------------------
def test_idle_layout_payload():
    p = P.idle_layout_payload(1000, 40, b"abc")
    total, offset = struct.unpack_from("<II", p)
    assert total == 1000 and offset == 40 and p[8:] == b"abc"


def test_mode_payload_bare_matches_spike():
    assert P.mode_payload(P.MODE_PANEL) == bytes([P.MODE_PANEL])
    assert P.mode_payload(P.MODE_HOST, 0, 0xFF) == bytes([P.MODE_HOST])


def test_mode_payload_timeout_only():
    assert P.mode_payload(P.MODE_PANEL, 60) == struct.pack("<BH", P.MODE_PANEL, 60)


def test_mode_payload_timeout_and_layout():
    assert P.mode_payload(P.MODE_PANEL, 300, 1) == struct.pack("<BHB", P.MODE_PANEL, 300, 1)


def test_mode_timeout_range_15_3600():
    # new lower bound is 15 (was 5)
    with pytest.raises(P.ProtocolError):
        P.mode_payload(P.MODE_PANEL, 5)
    with pytest.raises(P.ProtocolError):
        P.mode_payload(P.MODE_PANEL, 14)
    assert P.mode_payload(P.MODE_PANEL, 15) == struct.pack("<BH", P.MODE_PANEL, 15)
    assert P.mode_payload(P.MODE_PANEL, 3600) == struct.pack("<BH", P.MODE_PANEL, 3600)
    with pytest.raises(P.ProtocolError):
        P.mode_payload(P.MODE_PANEL, 3601)


def test_nak_names_14_15():
    assert P.NAK_NAMES[14] == "BAD_DATA" and P.NAK_NAMES[15] == "STORAGE"
