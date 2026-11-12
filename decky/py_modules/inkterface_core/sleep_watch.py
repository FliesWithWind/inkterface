"""Sleep hint: tell the panel to show its idle screens the moment the machine goes to sleep, and back on resume.

A thread subscribes to systemd-logind's PrepareForSleep / PrepareForShutdown on the system bus (stdlib client,
`dbus_min`) and holds a *delay* inhibitor lock. When the machine is about to sleep it calls `on_sleep()` (the
engine sends MODE PANEL and waits for the ACK), then releases the lock so the suspend goes ahead. On resume it
takes the lock again and calls `on_wake()`. Docs: docs/idle-mode.md "Sleep and wake".

The lock never holds a suspend longer than `budget_s` (logind itself gives up after InhibitDelayMaxUSec, 5 s by
default), whatever `on_sleep` does: it is released when the budget is spent, when `on_sleep` raises, and when
the bus connection dies.
"""
from __future__ import annotations

import logging
import os
import threading

from .dbus_min import Bus, BusError

log = logging.getLogger(__name__)

LOGIND, LOGIND_PATH, MANAGER = "org.freedesktop.login1", "/org/freedesktop/login1", "org.freedesktop.login1.Manager"
RULE = "type='signal',sender='%s',path='%s',interface='%s',member='%%s'" % (LOGIND, LOGIND_PATH, MANAGER)
MEMBERS = ("PrepareForSleep", "PrepareForShutdown")
SLEEP_BUDGET_S = 4.0     # < logind's 5 s delay limit
RETRY_MIN_S, RETRY_MAX_S = 1.0, 30.0
POLL_S = 0.5


class SleepWatcher:
    def __init__(self, on_sleep, on_wake, *, bus_factory=Bus, budget_s: float = SLEEP_BUDGET_S, who: str = "Inkterface",
                 retry_min_s: float = RETRY_MIN_S, retry_max_s: float = RETRY_MAX_S):
        self._on_sleep, self._on_wake = on_sleep, on_wake
        self._bus_factory, self.budget_s, self.who = bus_factory, budget_s, who
        self._retry = (retry_min_s, retry_max_s)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._fd: int | None = None
        self._owner: str | None = None
        self.connected = False
        self.last_error: str | None = None
        self.sleeps = self.wakes = 0

    @property
    def inhibited(self) -> bool:
        return self._fd is not None

    def start(self) -> None:
        if self._thread is None:
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="inkterface-sleep-hint", daemon=True)
            self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        t, self._thread = self._thread, None
        if t is not None:
            t.join(timeout)
        self._release()

    # -- thread -----------------------------------------------------------------------------
    def _run(self) -> None:
        delay = self._retry[0]
        while not self._stop.is_set():
            bus = None
            try:
                bus = self._bus_factory()
                bus.connect()
                self._owner = bus.call("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "GetNameOwner",
                                       "s", (LOGIND,))[0][0]
                for m in MEMBERS:
                    bus.add_match(RULE % m)
                self.connected, delay, self.last_error = True, self._retry[0], None
                log.info("sleep hint: watching logind on the system bus")
                self._take(bus)
                self._serve(bus)
            except (OSError, BusError, IndexError, TypeError) as e:
                self.last_error = f"{type(e).__name__}: {e}"
                log.warning("sleep hint: %s (retry in %.0f s)", self.last_error, delay)
            finally:
                self.connected = False
                self._release()
                if bus is not None:
                    bus.close()
            if self._stop.wait(delay):
                break
            delay = min(delay * 2, self._retry[1])

    def _serve(self, bus) -> None:
        while not self._stop.is_set():
            m = bus.signal(POLL_S)
            if (m is None or m.interface != MANAGER or m.member not in MEMBERS or m.path != LOGIND_PATH
                    or m.sender != self._owner or m.signature != "b" or len(m.body) != 1):
                continue   # not ours (or spoofed: only logind's own unique name is believed)
            if m.body[0]:
                self._going_down(m.member)
            else:
                self._back(bus)

    def _going_down(self, why: str) -> None:
        self.sleeps += 1
        log.info("sleep hint: %s(true): panel to idle", why)
        t = threading.Thread(target=self._call, args=(self._on_sleep,), name="inkterface-sleep-cb", daemon=True)
        t.start()
        t.join(self.budget_s)   # a stuck callback must not hold the suspend: release the lock anyway
        if t.is_alive():
            log.warning("sleep hint: panel did not answer within %.1f s, letting the machine sleep", self.budget_s)
        self._release()

    def _back(self, bus) -> None:
        self.wakes += 1
        log.info("sleep hint: resumed: panel back to host")
        self._call(self._on_wake)
        self._take(bus)

    @staticmethod
    def _call(fn) -> None:
        try:
            fn()
        except Exception:  # noqa: BLE001 - a failing callback must not kill the watcher or keep the lock
            log.exception("sleep hint callback failed")

    # -- inhibitor lock ---------------------------------------------------------------------
    def _take(self, bus) -> None:
        if self._fd is not None:
            return
        try:
            self._fd = bus.inhibit("sleep:shutdown", self.who, "Show the idle screens on the e-paper panel", "delay")
        except BusError as e:   # not allowed: the hint still works, it just cannot delay the suspend
            log.warning("sleep hint: no delay lock (%s)", e)

    def _release(self) -> None:
        fd, self._fd = self._fd, None
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
