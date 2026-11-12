"""Minimal D-Bus client over a unix socket: stdlib only (Decky's Python has no dbus bindings).

Just enough for the sleep hint (docs/idle-mode.md "Sleep and wake"): EXTERNAL auth with fd passing, method calls
(Hello, AddMatch, Inhibit), signal delivery. Single threaded: one thread owns a `Bus`.

Errors: `BusGone` (socket closed or the stream is no longer parseable: reconnect), `BusError` (the bus refused or
answered with an error), `Malformed` (one message could not be decoded: skipped, the stream is still in sync).
"""
from __future__ import annotations

import os
import select
import socket
import struct
import time
from collections import deque
from dataclasses import dataclass, field

SYSTEM_BUS = "/run/dbus/system_bus_socket"
MAX_MESSAGE = 1 << 20     # the spec allows 128 MiB; nothing we talk to sends a tenth of this
MAX_DEPTH = 16
CALL, RETURN, ERROR, SIGNAL = 1, 2, 3, 4
PATH, INTERFACE, MEMBER, ERROR_NAME, REPLY_SERIAL, DESTINATION, SENDER, SIGNATURE, UNIX_FDS = range(1, 10)
_FIELD_SIG = {PATH: "o", INTERFACE: "s", MEMBER: "s", ERROR_NAME: "s", REPLY_SERIAL: "u", DESTINATION: "s",
              SENDER: "s", SIGNATURE: "g", UNIX_FDS: "u"}


class BusGone(OSError):
    """Connection closed, or the byte stream is garbage: drop it and reconnect."""


class BusError(Exception):
    """The bus refused us or a method call returned an error."""


class Malformed(Exception):
    """One message is undecodable. The framing was fine, so the next message is still readable."""


@dataclass
class Message:
    type: int
    serial: int
    flags: int = 0
    path: str | None = None
    interface: str | None = None
    member: str | None = None
    error_name: str | None = None
    reply_serial: int | None = None
    destination: str | None = None
    sender: str | None = None
    signature: str = ""
    body: tuple = ()
    fds: list = field(default_factory=list)


# -- marshalling ------------------------------------------------------------------------------
_FIXED = {"y": ("B", 1), "b": ("I", 4), "n": ("h", 2), "q": ("H", 2), "i": ("i", 4), "u": ("I", 4), "h": ("I", 4),
          "x": ("q", 8), "t": ("Q", 8), "d": ("d", 8)}


class _W:
    def __init__(self):
        self.b = bytearray()

    def align(self, n):
        self.b += b"\0" * (-len(self.b) % n)

    def fixed(self, c, v):
        fmt, n = _FIXED[c]
        self.align(n)
        self.b += struct.pack("<" + fmt, int(v))

    def str_(self, v, c="s"):
        raw = v.encode("utf-8")
        if b"\0" in raw:
            raise ValueError("NUL in a D-Bus string")
        if c == "g":
            if len(raw) > 255:
                raise ValueError("signature too long")
            self.b += bytes([len(raw)]) + raw + b"\0"
        else:
            self.align(4)
            self.b += struct.pack("<I", len(raw)) + raw + b"\0"


def pack(sig: str, values) -> bytes:
    """Marshal basic types (y b n q i u h x t d s o g) only: all this client ever sends."""
    w = _W()
    if len(sig) != len(values):
        raise ValueError("signature and arguments differ in length")
    for c, v in zip(sig, values):
        if c in _FIXED:
            w.fixed(c, v)
        elif c in "sog":
            w.str_(v, c)
        else:
            raise ValueError(f"cannot send type {c!r}")
    return bytes(w.b)


def encode(msg: Message) -> bytes:
    fields = []
    for code, val in ((PATH, msg.path), (INTERFACE, msg.interface), (MEMBER, msg.member), (ERROR_NAME, msg.error_name),
                      (REPLY_SERIAL, msg.reply_serial), (DESTINATION, msg.destination), (SENDER, msg.sender),
                      (SIGNATURE, msg.signature or None)):
        if val is not None:
            fields.append((code, val))
    if msg.fds:
        fields.append((UNIX_FDS, len(msg.fds)))
    body = pack(msg.signature, msg.body) if msg.signature else b""
    w = _W()
    w.b += struct.pack("<cBBBII", b"l", msg.type, msg.flags, 1, len(body), msg.serial)
    w.align(4)
    w.b += b"\0\0\0\0"   # array length, patched below
    start = len(w.b)
    for code, val in fields:
        w.align(8)
        w.b.append(code)
        sig = _FIELD_SIG[code]
        w.str_(sig, "g")
        if sig == "u":
            w.fixed("u", val)
        else:
            w.str_(val, sig)
    struct.pack_into("<I", w.b, start - 4, len(w.b) - start)
    w.align(8)
    return bytes(w.b) + body


class _R:
    def __init__(self, data: bytes, little: bool, base: int = 0):
        self.d, self.p, self.e, self.base = data, 0, "<" if little else ">", base

    def align(self, n):
        pad = -(self.p + self.base) % n
        if self.p + pad > len(self.d) or any(self.d[self.p:self.p + pad]):
            raise Malformed("bad padding")
        self.p += pad

    def take(self, n):
        if n < 0 or self.p + n > len(self.d):
            raise Malformed("read past the end")
        r = self.d[self.p:self.p + n]
        self.p += n
        return r

    def fixed(self, c):
        fmt, n = _FIXED[c]
        self.align(n)
        return struct.unpack(self.e + fmt, self.take(n))[0]

    def string(self, c):
        if c == "g":
            n = self.take(1)[0]
        else:
            n = self.fixed("u")
        raw = self.take(n)
        if self.take(1) != b"\0":
            raise Malformed("string not NUL terminated")
        try:
            s = raw.decode("utf-8")
        except UnicodeDecodeError as e:
            raise Malformed("string is not UTF-8") from e
        if "\0" in s:
            raise Malformed("NUL inside a string")
        return s


def _end(sig: str, i: int, depth: int = 0) -> int:
    """Index just past the complete type starting at sig[i]."""
    if depth > MAX_DEPTH or i >= len(sig):
        raise Malformed("bad signature")
    c = sig[i]
    if c == "a":
        return _end(sig, i + 1, depth + 1)
    if c in "({":
        close = ")" if c == "(" else "}"
        j = i + 1
        while j < len(sig) and sig[j] != close:
            j = _end(sig, j, depth + 1)
        if j >= len(sig) or j == i + 1:
            raise Malformed("bad signature")
        return j + 1
    if c in _FIXED or c in "sogv":
        return i + 1
    raise Malformed(f"unknown type {c!r}")


def _value(r: _R, sig: str, i: int, depth: int):
    c = sig[i]
    if depth > MAX_DEPTH:
        raise Malformed("nesting too deep")
    if c in _FIXED:
        v = r.fixed(c)
        if c == "b":
            if v > 1:
                raise Malformed("bad boolean")
            return bool(v)
        return v
    if c in "sog":
        return r.string(c)
    if c == "v":
        vs = r.string("g")
        if _end(vs, 0) != len(vs):
            raise Malformed("variant signature is not one type")
        return _value(r, vs, 0, depth + 1)
    if c == "a":
        n = r.fixed("u")
        _end(sig, i + 1)
        r.align(_alignment(sig[i + 1]))
        if n > len(r.d) - r.p:
            raise Malformed("array longer than the message")
        stop, out = r.p + n, []
        while r.p < stop:
            out.append(_value(r, sig, i + 1, depth + 1))
        if r.p != stop:
            raise Malformed("array length does not match its elements")
        return out
    r.align(8)   # struct or dict entry
    out, j = [], i + 1
    while sig[j] not in ")}":
        out.append(_value(r, sig, j, depth + 1))
        j = _end(sig, j)
    return tuple(out)


def _alignment(c: str) -> int:
    return _FIXED[c][1] if c in _FIXED else 8 if c in "({" else 4 if c in "soa" else 1   # g and v align to 1


def unpack(sig: str, data: bytes, little: bool = True, base: int = 0) -> tuple:
    r = _R(data, little, base)
    out, i = [], 0
    while i < len(sig):
        j = _end(sig, i)   # validates the type before any byte is read
        out.append(_value(r, sig, i, 0))
        i = j
    if r.p != len(data):
        raise Malformed("trailing bytes after the body")
    return tuple(out)


def frame_length(head: bytes) -> int:
    """Total size of the message whose first 16 bytes are `head`. BusGone if it cannot be a message."""
    if head[0:1] not in (b"l", b"B"):
        raise BusGone("not a D-Bus stream (bad endianness byte)")
    e = "<" if head[0:1] == b"l" else ">"
    if head[3] != 1:
        raise BusGone("unsupported D-Bus protocol version")
    blen, _serial, flen = struct.unpack(e + "III", head[4:16])
    total = 16 + flen + (-(16 + flen) % 8) + blen
    if total > MAX_MESSAGE or flen > MAX_MESSAGE:
        raise BusGone("D-Bus message too large")
    return total


def decode(raw: bytes) -> Message:
    if len(raw) < 16 or raw[0:1] not in (b"l", b"B"):
        raise Malformed("not a message")
    little = raw[0:1] == b"l"
    e = "<" if little else ">"
    mtype, flags = raw[1], raw[2]
    blen, serial, flen = struct.unpack(e + "III", raw[4:16])
    head_end = 16 + flen
    if head_end > len(raw):
        raise Malformed("header fields longer than the message")
    if mtype not in (CALL, RETURN, ERROR, SIGNAL):
        raise Malformed(f"message type {mtype}")
    r = _R(raw[16:head_end], little, base=16)
    m = Message(mtype, serial, flags)
    seen = set()
    while r.p < len(r.d):
        r.align(8)
        code = r.take(1)[0]
        vs = r.string("g")
        if _end(vs, 0) != len(vs):
            raise Malformed("field signature is not one type")
        val = _value(r, vs, 0, 0)
        want = _FIELD_SIG.get(code)
        if want is None:
            continue   # unknown fields must be ignored
        if vs != want or code in seen:
            raise Malformed(f"bad header field {code}")
        seen.add(code)
        setattr(m, {PATH: "path", INTERFACE: "interface", MEMBER: "member", ERROR_NAME: "error_name",
                    REPLY_SERIAL: "reply_serial", DESTINATION: "destination", SENDER: "sender", SIGNATURE: "signature",
                    UNIX_FDS: "n_fds"}[code], val)
    need = {CALL: (PATH, MEMBER), SIGNAL: (PATH, INTERFACE, MEMBER), RETURN: (REPLY_SERIAL,), ERROR: (ERROR_NAME, REPLY_SERIAL)}
    if not all(f in seen for f in need[mtype]):
        raise Malformed("missing required header field")
    body = raw[head_end + (-head_end % 8):]
    if len(body) != blen:
        raise Malformed("body length mismatch")
    if any(raw[head_end:head_end + (-head_end % 8)]):
        raise Malformed("bad header padding")
    m.body = unpack(m.signature, body, little) if m.signature else ()
    if not m.signature and body:
        raise Malformed("body without a signature")
    return m


# -- connection -------------------------------------------------------------------------------
class Bus:
    def __init__(self, path: str = SYSTEM_BUS, uid: int | None = None, timeout: float = 5.0):
        self.path, self.uid, self.timeout = path, os.geteuid() if uid is None else uid, timeout
        self._sock: socket.socket | None = None
        self._buf = bytearray()
        self._fds: list = []
        self._serial = 0
        self._signals: deque = deque()
        self.name: str | None = None

    # -- setup
    def connect(self) -> None:
        """Connect, authenticate (EXTERNAL, with fd passing) and say Hello. Raises BusGone/BusError, leaves nothing open."""
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(self.timeout)
            self._sock = s
            s.connect(self.path)
            s.sendall(b"\0AUTH EXTERNAL " + str(self.uid).encode().hex().encode() + b"\r\n")
            line = self._line()
            if not line.startswith("OK"):
                raise BusError(f"authentication refused: {line[:60]}")
            s.sendall(b"NEGOTIATE_UNIX_FD\r\n")
            line = self._line()
            if not line.startswith("AGREE_UNIX_FD"):
                raise BusError(f"the bus will not pass file descriptors: {line[:60]}")
            s.sendall(b"BEGIN\r\n")
            s.settimeout(None)
            self.name = self.call("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "Hello")[0][0]
        except BaseException:
            self.close()
            raise

    def _line(self) -> str:
        deadline = time.monotonic() + self.timeout
        while b"\r\n" not in self._buf:
            if len(self._buf) > 4096:
                raise BusGone("authentication line too long")
            if time.monotonic() > deadline:
                raise BusGone("authentication timed out")
            try:
                data = self._sock.recv(256)
            except TimeoutError as e:
                raise BusGone("authentication timed out") from e
            if not data:
                raise BusGone("bus closed during authentication")
            self._buf += data
        line, _, rest = bytes(self._buf).partition(b"\r\n")
        self._buf = bytearray(rest)
        return line.decode("ascii", "replace")

    def close(self) -> None:
        s, self._sock = self._sock, None
        if s is not None:
            try:
                s.close()
            except OSError:
                pass
        for fd in self._fds:
            _close(fd)
        self._fds, self._buf = [], bytearray()

    # -- messages
    def _send(self, msg: Message) -> None:
        if self._sock is None:
            raise BusGone("not connected")
        try:
            self._sock.sendall(encode(msg))
        except OSError as e:
            raise BusGone(str(e)) from e

    def _fill(self, timeout: float | None) -> bool:
        """Read what is available (waiting up to `timeout`). False on timeout; BusGone on EOF or error."""
        if self._sock is None:
            raise BusGone("not connected")
        try:
            if not select.select([self._sock], [], [], timeout)[0]:
                return False
            data, fds, _flags, _addr = socket.recv_fds(self._sock, 65536, 16)
        except (OSError, ValueError) as e:
            raise BusGone(str(e)) from e
        self._fds += fds
        if not data:
            raise BusGone("bus closed the connection")
        self._buf += data
        return True

    def _take(self) -> Message | None:
        """One complete message out of the buffer, or None. Undecodable messages are skipped (their fds closed)."""
        while len(self._buf) >= 16:
            total = frame_length(bytes(self._buf[:16]))
            if len(self._buf) < total:
                return None
            raw, self._buf = bytes(self._buf[:total]), self._buf[total:]
            try:
                m = decode(raw)
                n = getattr(m, "n_fds", 0)
                if n > len(self._fds):
                    raise Malformed("announced more fds than arrived")
                m.fds, self._fds = self._fds[:n], self._fds[n:]
                return m
            except Malformed:
                continue
        if not self._buf:
            for fd in self._fds:   # fds nobody claimed
                _close(fd)
            self._fds = []
        return None

    def _next(self, timeout: float | None) -> Message | None:
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            m = self._take()
            if m is not None:
                return m
            left = None if deadline is None else max(0.0, deadline - time.monotonic())
            if not self._fill(left):
                return None

    def call(self, dest: str, path: str, iface: str, member: str, sig: str = "", args=()) -> tuple[tuple, list]:
        """Method call -> (body values, fds). BusError on a D-Bus error or no answer within `timeout`."""
        self._serial += 1
        serial = self._serial
        self._send(Message(CALL, serial, destination=dest, path=path, interface=iface, member=member, signature=sig, body=tuple(args)))
        deadline = time.monotonic() + self.timeout
        while True:
            m = self._next(max(0.0, deadline - time.monotonic()))
            if m is None:
                raise BusError(f"no answer to {member} within {self.timeout:g} s")
            if m.type == SIGNAL:
                self._signals.append(m)
            elif m.reply_serial == serial:
                if m.type == ERROR:
                    for fd in m.fds:
                        _close(fd)
                    raise BusError(f"{m.error_name}: {m.body[0] if m.body and isinstance(m.body[0], str) else ''}")
                if m.type == RETURN:
                    return m.body, m.fds
            else:
                for fd in m.fds:
                    _close(fd)

    def add_match(self, rule: str) -> None:
        self.call("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "AddMatch", "s", (rule,))

    def inhibit(self, what: str, who: str, why: str, mode: str = "delay") -> int:
        """systemd-logind Inhibit(): the returned fd IS the lock; os.close() it to release. BusError if refused."""
        body, fds = self.call("org.freedesktop.login1", "/org/freedesktop/login1", "org.freedesktop.login1.Manager", "Inhibit",
                              "ssss", (what, who, why, mode))
        if len(body) != 1 or not isinstance(body[0], int) or not 0 <= body[0] < len(fds):
            for fd in fds:
                _close(fd)
            raise BusError("Inhibit returned no file descriptor")
        keep = fds[body[0]]
        for i, fd in enumerate(fds):
            if i != body[0]:
                _close(fd)
        return keep

    def signal(self, timeout: float | None) -> Message | None:
        """Next signal (waiting up to `timeout`), None on timeout. Replies and calls addressed to us are dropped."""
        while True:
            if self._signals:
                return self._signals.popleft()
            m = self._next(timeout)
            if m is None:
                return None
            if m.type == SIGNAL:
                return m
            for fd in m.fds:
                _close(fd)


def _close(fd: int) -> None:
    try:
        os.close(fd)
    except OSError:
        pass
