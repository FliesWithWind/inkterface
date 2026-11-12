"""In-memory serial link to an EmulatedPanel, with the real panel's timing quirks.

Same read/write/close surface as `serialport.SerialPort`, so the engine cannot tell it from a
real port. Used by the tests and by `decky/tools/run_engine.py --emulator`.

Like the firmware, the panel is deaf while it refreshes the e-paper: bytes written meanwhile
queue up and are processed when the refresh ends. `max_backlog` records the most bytes that were
ever queued unprocessed (a real panel drops bytes past its 16 KiB RX queue).
"""
from __future__ import annotations

import errno
import time
from collections import deque

from .. import proto as P
from ..emulator import EmulatedPanel


class EmulatedLink:
    def __init__(self, panel: EmulatedPanel | None = None, clock=time.monotonic, partial_s: float = 0.0, full_s: float = 0.0):
        self.panel = panel or EmulatedPanel()
        self.clock = clock
        self.partial_s, self.full_s = partial_s, full_s
        self.closed = False
        self.dead = False  # unplugged: every call raises EIO
        self.mute = False  # panel hung: replies are dropped
        self.fail_after: int | None = None  # unplug once this many more bytes have been written
        self.written: list[P.Frame] = []  # every host frame the panel parsed, in order
        self.max_backlog = 0
        self.max_pending_refresh = 0
        self._dec = P.Decoder()  # splits the host stream into frames so REFRESH can take time
        self._frames: deque = deque()  # (frame, wire bytes) waiting for the panel
        self._busy_until = 0.0
        self._out: deque = deque()  # (ready_at, bytes) panel -> host
        self._backlog = 0
        # A fresh port means a fresh stream; the panel drops a half frame after 100 ms of silence.
        self.panel._dec.flush_stale()

    # -- test controls ------------------------------------------------------------
    def unplug(self) -> None:
        self.dead = True

    # -- SerialPort surface -------------------------------------------------------
    def write(self, data: bytes, timeout=None) -> None:
        self._check()
        if self.fail_after is not None:
            if len(data) >= self.fail_after:
                data, self.fail_after = data[: self.fail_after], None
                self._feed(data)
                self.dead = True
                raise OSError(errno.EIO, "unplugged mid-write")
            self.fail_after -= len(data)
        self._feed(data)

    def read(self, size: int = 4096, timeout=None) -> bytes:
        self._check()
        self._run()
        now = self.clock()
        out = bytearray()
        while self._out and self._out[0][0] <= now and len(out) < size:
            at, chunk = self._out.popleft()
            room = size - len(out)
            out += chunk[:room]
            if len(chunk) > room:
                self._out.appendleft((at, chunk[room:]))
        return bytes(out)

    def close(self) -> None:
        self.closed = True

    # -- internals ----------------------------------------------------------------
    def _check(self) -> None:
        if self.dead:
            raise OSError(errno.EIO, "serial device disconnected")
        if self.closed:
            raise ValueError("port is closed")

    def _feed(self, data: bytes) -> None:
        for f in self._dec.feed(data):
            self._frames.append((f, P.encode(f.type, f.seq, f.payload)))
        self._backlog += len(data)
        self.max_backlog = max(self.max_backlog, self._backlog)
        self._run()

    def _run(self) -> None:
        while self._frames and self.clock() >= self._busy_until:
            f, wire = self._frames.popleft()
            self._backlog -= len(wire)
            self.written.append(f)
            reply = self.panel.feed(wire)
            now = self.clock()
            if f.type == P.T_REFRESH and f.payload:
                dur = self.full_s if f.payload[0] == 1 else self.partial_s
                self._busy_until = now + dur
            if reply and not self.mute:
                self._out.append((self._busy_until if f.type == P.T_REFRESH else now, reply))
        self.max_pending_refresh = max(
            self.max_pending_refresh,
            sum(1 for f, _ in self._frames if f.type == P.T_REFRESH) + (1 if self.clock() < self._busy_until else 0),
        )
