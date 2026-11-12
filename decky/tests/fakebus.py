"""A fake D-Bus system bus on a unix socket: EXTERNAL auth with fd passing, Hello / AddMatch / GetNameOwner / Inhibit.

Used by tests/test_sleep_idle.py and tools/sleep_test.py to drive the real `dbus_min` client and `SleepWatcher` with
logind's PrepareForSleep, which a test can neither emit on the real bus nor trigger by suspending the machine.
`Inhibit` replies carry a real fd (a pipe's write end, SCM_RIGHTS), so "the lock is held / released" is observable.
"""
from __future__ import annotations

import os
import select
import socket
import threading
import time

from inkterface_core import dbus_min as D

LOGIND_OWNER = ":1.6"


class Conn:
    def __init__(self, bus: "FakeBus", sock: socket.socket, number: int):
        self.bus, self.sock, self.number = bus, sock, number   # number: 1-based, fixed before the thread can reply (was len(conns): racy)
        self.buf = bytearray()
        self.auth_lines: list[str] = []
        self.calls: list[D.Message] = []
        self.locks: list[dict] = []   # {"args": (...), "r": read end of the pipe whose write end the client got}
        self.ready = threading.Event()
        self.serial = 0
        self.closed = False
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    # -- server side ------------------------------------------------------------------------
    def _line(self) -> str | None:
        while b"\r\n" not in self.buf:
            d = self.sock.recv(4096)
            if not d:
                return None
            self.buf += d
        line, _, rest = bytes(self.buf).partition(b"\r\n")
        self.buf = bytearray(rest)
        return line.decode()

    def _run(self) -> None:
        try:
            if self.sock.recv(1) != b"\0":   # the credentials byte that starts every connection
                return
            line = self._line()
            if line is None:
                return
            self.auth_lines.append(line)
            if self.bus.auth_reply is not None:
                self.sock.sendall(self.bus.auth_reply)
                if not self.bus.auth_reply.startswith(b"OK"):
                    if self.bus.hold_after_bad_auth:
                        time.sleep(self.bus.hold_after_bad_auth)
                    return
            else:
                self.sock.sendall(b"OK 0123456789abcdef0123456789abcdef\r\n")
            line = self._line()
            if line is None:
                return
            self.auth_lines.append(line)
            self.sock.sendall(b"AGREE_UNIX_FD\r\n" if self.bus.agree_fd else b"ERROR \"no fds\"\r\n")
            if not self.bus.agree_fd:
                return
            line = self._line()
            if line is None:
                return
            self.auth_lines.append(line)
            self.ready.set()
            self._messages()
        except OSError:
            pass
        finally:
            self.closed = True
            self.ready.set()

    def _messages(self) -> None:
        while True:
            while len(self.buf) >= 16 and len(self.buf) >= D.frame_length(bytes(self.buf[:16])):
                total = D.frame_length(bytes(self.buf[:16]))
                raw, self.buf = bytes(self.buf[:total]), self.buf[total:]
                m = D.decode(raw)
                self.calls.append(m)
                self.bus.on_call(self, m)
            d = self.sock.recv(65536)
            if not d:
                return
            self.buf += d

    def reply(self, call: D.Message, sig: str = "", body=(), fds=()) -> None:
        self._send(D.Message(D.RETURN, self._next(), reply_serial=call.serial, signature=sig, body=tuple(body)), fds)

    def error(self, call: D.Message, name: str, text: str) -> None:
        self._send(D.Message(D.ERROR, self._next(), error_name=name, reply_serial=call.serial, signature="s", body=(text,)))

    def signal(self, member: str, value: bool, sender: str = LOGIND_OWNER, path: str = "/org/freedesktop/login1",
               iface: str = "org.freedesktop.login1.Manager") -> None:
        self._send(D.Message(D.SIGNAL, self._next(), path=path, interface=iface, member=member, sender=sender,
                             signature="b", body=(value,)))

    def raw(self, data: bytes) -> None:
        with self.lock:
            self.sock.sendall(data)

    def _next(self) -> int:
        self.serial += 1
        return self.serial

    def _send(self, msg: D.Message, fds=()) -> None:
        msg.sender = msg.sender or "org.freedesktop.DBus"
        wire = D.encode(D.Message(**{**msg.__dict__, "fds": list(fds)}))
        with self.lock:
            if fds:
                socket.send_fds(self.sock, [wire], list(fds))
            else:
                self.sock.sendall(wire)

    def close(self) -> None:
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()

    # -- test side --------------------------------------------------------------------------
    def lock_released(self, i: int = -1) -> bool:
        """True once the client closed (every copy of) the fd it was handed for lock `i`."""
        r = self.locks[i]["r"]
        if not select.select([r], [], [], 0)[0]:
            return False
        return os.read(r, 1) == b""


class FakeBus:
    def __init__(self, path: str | None = None, *, auth_reply: bytes | None = None, agree_fd: bool = True, inhibit_error: str | None = None,
                 hold_after_bad_auth: float = 0.0):
        self.path = path or "\0inkterface-fakebus-%d-%d" % (os.getpid(), time.monotonic_ns())   # abstract socket: no path length limit
        self.auth_reply, self.agree_fd, self.inhibit_error = auth_reply, agree_fd, inhibit_error
        self.hold_after_bad_auth = hold_after_bad_auth
        self.conns: list[Conn] = []
        self.matches: list[str] = []
        self.extra_fds_on_inhibit = 0
        self.inhibit_without_fd = False
        self._srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._srv.bind(self.path)
        self._srv.listen(8)
        self._stop = False
        self._t = threading.Thread(target=self._accept, daemon=True)
        self._t.start()

    def _accept(self) -> None:
        while not self._stop:
            try:
                s, _ = self._srv.accept()
            except OSError:
                return
            self.conns.append(Conn(self, s, len(self.conns) + 1))   # only this thread appends

    def stop(self) -> None:
        self._stop = True
        try:
            self._srv.close()
        except OSError:
            pass
        for c in self.conns:
            c.close()

    def wait_conn(self, n: int = 1, timeout: float = 5.0) -> Conn:
        """The n-th connection that finished authentication."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if len(self.conns) >= n and self.conns[n - 1].ready.is_set() and not self.conns[n - 1].closed:
                return self.conns[n - 1]
            time.sleep(0.005)
        raise AssertionError(f"connection {n} did not come up")

    # -- the bus's behaviour ----------------------------------------------------------------
    def on_call(self, c: Conn, m: D.Message) -> None:
        if m.type != D.CALL:
            return
        if m.member == "Hello":
            c.reply(m, "s", (":1.%d" % (40 + c.number),))
        elif m.member == "AddMatch":
            self.matches.append(m.body[0])
            c.reply(m)
        elif m.member == "GetNameOwner":
            c.reply(m, "s", (LOGIND_OWNER,))
        elif m.member == "Inhibit":
            if self.inhibit_error:
                return c.error(m, self.inhibit_error, "not allowed")
            if self.inhibit_without_fd:
                return c.reply(m, "u", (7,))
            r, w = os.pipe()
            extra = [os.pipe() for _ in range(self.extra_fds_on_inhibit)]
            c.locks.append({"args": m.body, "r": r})
            fds = [w] + [x[1] for x in extra]
            try:
                c.reply(m, "h", (0,), fds=fds)
            finally:
                for fd in fds:
                    os.close(fd)
                for x in extra:
                    os.close(x[0])
        else:
            c.error(m, "org.freedesktop.DBus.Error.UnknownMethod", "no such method")
