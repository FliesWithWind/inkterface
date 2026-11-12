"""Sleep -> idle screens, resume -> active screens (docs/idle-mode.md "Sleep and wake").

Layers, bottom up:
  * dbus_min      the stdlib D-Bus client, against a fake bus socket (fakebus.py) and hand-built wire bytes
  * SleepWatcher  logind PrepareForSleep/PrepareForShutdown + the delay inhibitor lock, against the same fake bus
  * Engine        prepare_sleep()/wake() over the panel emulator
  * PluginCore    the plugin wires the watcher to the engine and stops it first
  * firmware      the bus-suspend detector, compiled with g++ (firmware/host_test/usb_bus_watch_host.cpp)
"""
import os
import random
import shutil
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "engine"))
from engine_testlib import Rig  # noqa: E402
from fakebus import LOGIND_OWNER, FakeBus  # noqa: E402

from inkterface_core import dbus_min as D  # noqa: E402
from inkterface_core import proto as P  # noqa: E402
from inkterface_core.engine import engine as E  # noqa: E402
from inkterface_core.sleep_watch import SleepWatcher  # noqa: E402

SIGNAL_RULE = "type='signal',sender='org.freedesktop.login1',path='/org/freedesktop/login1'," \
              "interface='org.freedesktop.login1.Manager',member='%s'"


def until(pred, timeout=5.0, what="condition"):
    end = time.monotonic() + timeout
    while not pred():
        assert time.monotonic() < end, f"{what} not reached in {timeout} s"
        time.sleep(0.005)


def open_fds() -> int:
    return len(os.listdir("/proc/self/fd"))


@pytest.fixture
def bus(tmp_path):
    b = FakeBus()
    yield b
    b.stop()


def client(bus, **kw):
    return D.Bus(bus.path, uid=1000, timeout=kw.pop("timeout", 2.0))


# =============================================================================== dbus_min: wire format
def hand_message(mtype, body_sig, body, fields, *, big=False, serial=9, flags=0):
    """A message assembled byte by byte (not via dbus_min.encode), optionally big endian. fields: [(code, sig, value)]."""
    e = ">" if big else "<"

    def align(b, n):
        b += b"\0" * (-len(b) % n)

    f = bytearray()
    for code, sig, val in fields:
        align(f, 8)
        f += bytes([code, len(sig)]) + sig.encode() + b"\0"
        if sig == "u":
            align(f, 4)
            f += struct.pack(e + "I", val)
        elif sig == "g":
            f += bytes([len(val)]) + val.encode() + b"\0"
        else:
            align(f, 4)
            f += struct.pack(e + "I", len(val)) + val.encode() + b"\0"
    head = (b"B" if big else b"l") + bytes([mtype, flags, 1]) + struct.pack(e + "III", len(body), serial, len(f))
    head += bytes(f)
    head += b"\0" * (-len(head) % 8)
    return head + body


SIGNAL_FIELDS = [(1, "o", "/org/freedesktop/login1"), (2, "s", "org.freedesktop.login1.Manager"), (3, "s", "PrepareForSleep"),
                 (8, "g", "b")]


@pytest.mark.parametrize("big", [False, True])
def test_decode_a_handmade_signal_in_either_byte_order(big):
    body = struct.pack(">I" if big else "<I", 1)
    m = D.decode(hand_message(D.SIGNAL, "b", body, SIGNAL_FIELDS, big=big))
    assert (m.type, m.serial, m.member, m.interface, m.path, m.signature, m.body) == \
        (D.SIGNAL, 9, "PrepareForSleep", "org.freedesktop.login1.Manager", "/org/freedesktop/login1", "b", (True,))
    assert D.frame_length(hand_message(D.SIGNAL, "b", body, SIGNAL_FIELDS, big=big)[:16]) == len(hand_message(D.SIGNAL, "b", body, SIGNAL_FIELDS, big=big))


def test_encode_matches_the_handmade_bytes():
    m = D.Message(D.SIGNAL, 9, path="/org/freedesktop/login1", interface="org.freedesktop.login1.Manager",
                  member="PrepareForSleep", signature="b", body=(True,))
    assert D.encode(m) == hand_message(D.SIGNAL, "b", struct.pack("<I", 1), SIGNAL_FIELDS)


def test_unknown_header_fields_are_ignored():
    m = D.decode(hand_message(D.SIGNAL, "b", struct.pack("<I", 0), SIGNAL_FIELDS + [(99, "s", "future")]))
    assert m.body == (False,)


@pytest.mark.parametrize("sig,args,expect", [
    ("s", ("héllo",), ("héllo",)), ("ssss", ("a", "", "c", "d"), ("a", "", "c", "d")),
    ("ubo", (7, True, "/x"), (7, True, "/x")), ("yqiuxt", (1, 2, -3, 4, -5, 6), (1, 2, -3, 4, -5, 6)),
    ("g", ("a{sv}",), ("a{sv}",)), ("h", (3,), (3,))])
def test_pack_unpack_round_trip(sig, args, expect):
    assert D.unpack(sig, D.pack(sig, args)) == expect


def test_unpack_containers_variants_and_dicts():
    # a{sv} with one entry {"k": <u 5>}, then a (is) struct, as the bus sends them
    w = D._W()
    w.align(4)
    w.b += b"\0\0\0\0"
    w.align(8)             # the array length does not count the padding up to the first element
    start = len(w.b)
    w.str_("k")
    w.str_("u", "g")
    w.fixed("u", 5)
    struct.pack_into("<I", w.b, 0, len(w.b) - start)
    w.align(8)
    w.fixed("i", -2)
    w.str_("two")
    assert D.unpack("a{sv}(is)", bytes(w.b)) == ([("k", 5)], (-2, "two"))


@pytest.mark.parametrize("sig,data", [
    ("s", struct.pack("<I", 5) + b"abc\0"),                       # string longer than the data
    ("s", struct.pack("<I", 3) + b"abc!"),                        # not NUL terminated
    ("s", struct.pack("<I", 3) + b"a\0c\0"),                      # NUL inside
    ("s", struct.pack("<I", 2) + b"\xff\xfe\0"),                  # not UTF-8
    ("b", struct.pack("<I", 2)),                                  # boolean 2
    ("as", struct.pack("<I", 1000) + b"\0" * 8),                  # array longer than the message
    ("ai", struct.pack("<I", 3) + b"\0" * 4),                     # array length not a multiple of the element
    ("u", b"\0\0"),                                               # truncated
    ("y", b"\1\2"),                                               # trailing bytes
    ("v", b"\x01u\0" + struct.pack("<I", 1)[:3]),                 # variant value cut short
    ("v", b"\x02uu\0"),                                           # variant signature with two types
    ("a" * 40 + "y", struct.pack("<I", 0) * 3),                   # nesting too deep
    ("(", b""), ("a", b""), ("{sv", b""), ("z", b"\0" * 8),       # bad signatures
    ("y", b"")])
def test_malformed_bodies_raise_only_Malformed(sig, data):
    with pytest.raises(D.Malformed):
        D.unpack(sig, data)


@pytest.mark.parametrize("name,msg", [
    ("body length lies", hand_message(D.SIGNAL, "b", struct.pack("<I", 1), SIGNAL_FIELDS)[:-1] + b""),
    ("unknown message type", hand_message(9, "b", struct.pack("<I", 1), SIGNAL_FIELDS)),
    ("signal without a member", hand_message(D.SIGNAL, "b", struct.pack("<I", 1), [SIGNAL_FIELDS[0], SIGNAL_FIELDS[1], SIGNAL_FIELDS[3]])),
    ("member of the wrong type", hand_message(D.SIGNAL, "b", struct.pack("<I", 1), [SIGNAL_FIELDS[0], SIGNAL_FIELDS[1], (3, "u", 4), SIGNAL_FIELDS[3]])),
    ("duplicate field", hand_message(D.SIGNAL, "b", struct.pack("<I", 1), SIGNAL_FIELDS + [(3, "s", "Again")])),
    ("body without signature", hand_message(D.SIGNAL, "", struct.pack("<I", 1), SIGNAL_FIELDS[:3])),
    ("return without reply serial", hand_message(D.RETURN, "", b"", [])),
    ("field signature with two types", hand_message(D.SIGNAL, "b", struct.pack("<I", 1), SIGNAL_FIELDS[:3] + [(8, "gg", "b")]))])
def test_malformed_messages_raise_only_Malformed(name, msg):
    if name == "body length lies":
        msg = hand_message(D.SIGNAL, "b", struct.pack("<I", 1), SIGNAL_FIELDS) + b"\0"   # one byte more than announced
    with pytest.raises(D.Malformed):
        D.decode(msg)


@pytest.mark.parametrize("head", [
    b"x" + b"\0" * 15,                                             # not a D-Bus stream
    b"l\x04\0\x02" + b"\0" * 12,                                   # protocol version 2
    b"l\x04\0\x01" + struct.pack("<III", 2 << 20, 1, 0),           # body over the cap
    b"l\x04\0\x01" + struct.pack("<III", 0, 1, 2 << 20)])          # fields over the cap
def test_framing_garbage_is_fatal(head):
    with pytest.raises(D.BusGone):
        D.frame_length(head)


def test_fuzz_decode_never_raises_anything_but_Malformed():
    rng = random.Random(1234)
    seeds = [D.encode(D.Message(D.SIGNAL, 3, path="/a/b", interface="i.f", member="M", signature="sbu", body=("x", True, 7))),
             D.encode(D.Message(D.RETURN, 4, reply_serial=2, signature="h", body=(0,), fds=[5])),
             D.encode(D.Message(D.ERROR, 5, error_name="a.B", reply_serial=2, signature="s", body=("boom",))),
             hand_message(D.SIGNAL, "b", struct.pack(">I", 1), SIGNAL_FIELDS, big=True)]
    ok = bad = 0
    for _ in range(4000):
        raw = bytearray(rng.choice(seeds))
        for _ in range(rng.randint(1, 4)):
            op = rng.random()
            if op < 0.6 and raw:
                raw[rng.randrange(len(raw))] = rng.randrange(256)
            elif op < 0.8 and raw:
                del raw[rng.randrange(len(raw)):]
            else:
                raw += bytes(rng.randrange(256) for _ in range(rng.randint(1, 9)))
        try:
            D.decode(bytes(raw))
            ok += 1
        except D.Malformed:
            bad += 1
    assert ok and bad


# =============================================================================== dbus_min: a connection
def test_connect_authenticates_with_the_uid_negotiates_fds_and_says_hello(bus):
    b = client(bus)
    b.connect()
    c = bus.wait_conn()
    assert c.auth_lines == ["AUTH EXTERNAL " + b"1000".hex(), "NEGOTIATE_UNIX_FD", "BEGIN"]
    assert b.name == ":1.41" and [m.member for m in c.calls] == ["Hello"]
    assert (c.calls[0].destination, c.calls[0].path, c.calls[0].interface) == \
        ("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus")
    b.close()


def test_the_default_identity_is_the_effective_uid(bus):
    b = D.Bus(bus.path)
    b.connect()
    assert bus.wait_conn().auth_lines[0] == "AUTH EXTERNAL " + str(os.geteuid()).encode().hex()
    b.close()


def test_no_socket_is_an_OSError(tmp_path):
    with pytest.raises(OSError):
        D.Bus("\0inkterface-nobody-home").connect()


@pytest.mark.parametrize("kw,exc,text", [
    ({"auth_reply": b"REJECTED EXTERNAL\r\n"}, D.BusError, "authentication refused"),
    ({"agree_fd": False}, D.BusError, "file descriptors"),
    ({"auth_reply": b"x" * 5000}, D.BusGone, "too long"),
])
def test_refused_auth_or_no_fd_passing_fails_cleanly(tmp_path, kw, exc, text):
    b = FakeBus(**kw)
    try:
        cl = client(b)
        with pytest.raises(exc, match=text):
            cl.connect()
        assert cl._sock is None     # closed again, nothing half open
    finally:
        b.stop()


def test_bus_closing_during_auth_is_BusGone(tmp_path):
    b = FakeBus(auth_reply=b"")   # says nothing and hangs up
    try:
        with pytest.raises(D.BusGone):
            client(b).connect()
    finally:
        b.stop()


def test_silent_bus_times_out(tmp_path):
    b = FakeBus(auth_reply=b"OK x", hold_after_bad_auth=3)   # no CRLF: never completes
    try:
        t0 = time.monotonic()
        with pytest.raises(D.BusGone):
            client(b, timeout=0.3).connect()
        assert time.monotonic() - t0 < 2
    finally:
        b.stop()


def test_add_match_and_signal_delivery(bus):
    b = client(bus)
    b.connect()
    rule = SIGNAL_RULE % "PrepareForSleep"
    b.add_match(rule)
    c = bus.wait_conn()
    assert bus.matches == [rule] and c.calls[-1].signature == "s"
    c.signal("PrepareForSleep", True)
    m = b.signal(2.0)
    assert (m.member, m.body, m.sender) == ("PrepareForSleep", (True,), LOGIND_OWNER)
    assert b.signal(0.05) is None
    c.signal("PrepareForSleep", False)
    assert b.signal(2.0).body == (False,)
    b.close()


def test_signals_that_arrive_while_a_call_waits_are_not_lost(bus):
    b = client(bus)
    b.connect()
    c = bus.wait_conn()
    orig = bus.on_call

    def chatty(conn, m):
        if m.member == "GetNameOwner":
            conn.signal("PrepareForSleep", True)    # lands before the reply
            conn.signal("PrepareForShutdown", False)
        orig(conn, m)

    bus.on_call = chatty
    assert b.call("org.freedesktop.DBus", "/", "org.freedesktop.DBus", "GetNameOwner", "s", ("x",))[0] == (LOGIND_OWNER,)
    assert [(m.member, m.body) for m in (b.signal(1), b.signal(1))] == [("PrepareForSleep", (True,)), ("PrepareForShutdown", (False,))]
    assert c
    b.close()


def test_a_dbus_error_reply_raises_BusError(bus):
    b = client(bus)
    b.connect()
    with pytest.raises(D.BusError, match="UnknownMethod"):
        b.call("x.y", "/", "x.y", "Nope")
    b.close()


def test_unanswered_call_times_out(bus):
    b = client(bus, timeout=0.2)
    b.connect()
    bus.on_call = lambda conn, m: None
    t0 = time.monotonic()
    with pytest.raises(D.BusError, match="no answer"):
        b.call("x.y", "/", "x.y", "Slow")
    assert time.monotonic() - t0 < 2
    b.close()


def test_undecodable_messages_are_skipped_and_the_stream_stays_in_sync(bus):
    b = client(bus)
    b.connect()
    c = bus.wait_conn()
    good = D.encode(D.Message(D.SIGNAL, 5, path="/p", interface="i.f", member="Ok", signature="b", body=(True,)))
    for bad in (hand_message(D.SIGNAL, "b", struct.pack("<I", 7), SIGNAL_FIELDS),                       # boolean 7
                hand_message(D.SIGNAL, "b", struct.pack("<I", 1), SIGNAL_FIELDS[:2]),                    # no member
                hand_message(D.SIGNAL, "s", struct.pack("<I", 99) + b"x", SIGNAL_FIELDS[:3] + [(8, "g", "s")])):
        c.raw(bad)
    c.raw(good)
    assert b.signal(2.0).member == "Ok"
    b.close()


def test_a_message_split_across_reads_is_reassembled(bus):
    b = client(bus)
    b.connect()
    c = bus.wait_conn()
    wire = D.encode(D.Message(D.SIGNAL, 5, path="/p", interface="i.f", member="Split", signature="b", body=(True,)))
    for i in range(0, len(wire), 7):
        c.raw(wire[i:i + 7])
        time.sleep(0.002)
    assert b.signal(2.0).member == "Split"
    c.raw(wire + wire)   # two in one read
    assert [b.signal(1).member, b.signal(1).member] == ["Split", "Split"]
    b.close()


@pytest.mark.parametrize("garbage", [b"x" * 40, b"l\x04\0\x01" + struct.pack("<III", 5 << 20, 1, 0) + b"\0" * 8])
def test_a_garbled_stream_is_BusGone(bus, garbage):
    b = client(bus)
    b.connect()
    bus.wait_conn().raw(garbage)
    with pytest.raises(D.BusGone):
        b.signal(2.0)
    b.close()


def test_bus_gone_mid_stream_and_after_close(bus):
    b = client(bus)
    b.connect()
    bus.wait_conn().close()
    with pytest.raises(D.BusGone):
        b.signal(2.0)
    b.close()
    b.close()   # twice is fine
    with pytest.raises(D.BusGone):
        b.signal(0.1)
    with pytest.raises(D.BusGone):
        b.add_match("x")


def test_inhibit_hands_over_the_lock_fd_and_closing_it_releases_it(bus):
    b = client(bus)
    b.connect()
    c = bus.wait_conn()
    fd = b.inhibit("sleep:shutdown", "Inkterface", "why", "delay")
    assert c.locks[0]["args"] == ("sleep:shutdown", "Inkterface", "why", "delay")
    assert not c.lock_released()
    os.close(fd)
    until(c.lock_released, what="lock released")
    b.close()


def test_inhibit_errors_and_missing_fd(bus):
    b = client(bus)
    b.connect()
    bus.inhibit_error = "org.freedesktop.DBus.Error.AccessDenied"
    with pytest.raises(D.BusError, match="AccessDenied"):
        b.inhibit("sleep", "w", "y")
    bus.inhibit_error = None
    bus.inhibit_without_fd = True
    with pytest.raises(D.BusError, match="no file descriptor"):
        b.inhibit("sleep", "w", "y")
    b.close()


def test_no_fd_leaks_even_when_the_bus_sends_extra_ones(bus):
    b = client(bus)
    b.connect()
    bus.extra_fds_on_inhibit = 3
    base = open_fds()
    for _ in range(5):
        os.close(b.inhibit("sleep", "w", "y"))
    # the fake bus keeps one read end per lock (5); a client that kept the 3 extra fds per reply would add 15 more
    until(lambda: open_fds() <= base + 5, what="no leaked fds")
    time.sleep(0.1)
    assert open_fds() <= base + 5
    b.close()


# =============================================================================== SleepWatcher
class Hooks:
    def __init__(self, bus=None):
        self.events, self.bus = [], bus
        self.lock_state_during_sleep = None
        self.block = None
        self.fail = False

    def on_sleep(self):
        self.events.append("sleep")
        if self.bus is not None and self.bus.conns:
            self.lock_state_during_sleep = self.bus.conns[-1].lock_released()
        if self.block:
            self.block.wait(10)
        if self.fail:
            raise RuntimeError("engine blew up")

    def on_wake(self):
        self.events.append("wake")


@pytest.fixture
def hooks(bus):
    return Hooks(bus)


def watcher(bus, hooks, **kw):
    kw.setdefault("budget_s", 1.0)
    w = SleepWatcher(hooks.on_sleep, hooks.on_wake, bus_factory=lambda: D.Bus(bus.path, uid=1000, timeout=2.0),
                     retry_min_s=0.05, retry_max_s=0.2, **kw)
    w.start()
    return w


def up(bus, w, n=1):
    """The n-th connection is fully set up: matches registered, lock taken."""
    c = bus.wait_conn(n)
    until(lambda: w.connected and len(c.locks) >= 1, what="watcher up")
    return c


def test_it_watches_logind_and_holds_a_delay_lock(bus, hooks):
    w = watcher(bus, hooks)
    try:
        c = up(bus, w)
        assert bus.matches == [SIGNAL_RULE % "PrepareForSleep", SIGNAL_RULE % "PrepareForShutdown"]
        assert c.locks[0]["args"][0] == "sleep:shutdown" and c.locks[0]["args"][3] == "delay"
        assert c.locks[0]["args"][1] == "Inkterface" and w.inhibited and w.last_error is None
        assert not c.lock_released()
    finally:
        w.stop()


def test_sleep_calls_the_engine_while_the_lock_is_held_then_releases_it_and_resume_takes_it_again(bus, hooks):
    w = watcher(bus, hooks)
    try:
        c = up(bus, w)
        c.signal("PrepareForSleep", True)
        until(lambda: c.lock_released(0), what="lock released after the engine answered")
        assert hooks.events == ["sleep"] and hooks.lock_state_during_sleep is False   # held while the panel was told
        assert not w.inhibited
        c.signal("PrepareForSleep", False)
        until(lambda: len(c.locks) == 2, what="lock taken again on resume")
        assert hooks.events == ["sleep", "wake"] and not c.lock_released(1) and w.inhibited
        assert (w.sleeps, w.wakes) == (1, 1)
    finally:
        w.stop()


def test_shutdown_is_handled_like_sleep_and_an_aborted_shutdown_like_a_resume(bus, hooks):
    w = watcher(bus, hooks)
    try:
        c = up(bus, w)
        c.signal("PrepareForShutdown", True)
        until(lambda: c.lock_released(0))
        c.signal("PrepareForShutdown", False)
        until(lambda: hooks.events == ["sleep", "wake"] and len(c.locks) == 2)
    finally:
        w.stop()


def test_signals_not_from_logind_are_ignored(bus, hooks):
    w = watcher(bus, hooks)
    try:
        c = up(bus, w)
        c.signal("PrepareForSleep", True, sender=":1.99")                                   # someone else's unique name
        c.signal("PrepareForSleep", True, path="/org/freedesktop/other")
        c.signal("PrepareForSleep", True, iface="org.freedesktop.login1.Session")
        c.signal("Unrelated", True)
        c.raw(D.encode(D.Message(D.SIGNAL, 40, path="/org/freedesktop/login1", interface="org.freedesktop.login1.Manager",
                                 member="PrepareForSleep", sender=LOGIND_OWNER, signature="u", body=(1,))))   # wrong type
        c.raw(D.encode(D.Message(D.SIGNAL, 41, path="/org/freedesktop/login1", interface="org.freedesktop.login1.Manager",
                                 member="PrepareForSleep", sender=LOGIND_OWNER, signature="bb", body=(True, True))))
        c.signal("PrepareForSleep", True)                                                   # the real one, last
        until(lambda: hooks.events == ["sleep"])
        time.sleep(0.1)
        assert hooks.events == ["sleep"]
    finally:
        w.stop()


def test_a_failing_engine_call_still_releases_the_lock_and_the_watcher_lives(bus, hooks):
    hooks.fail = True
    w = watcher(bus, hooks)
    try:
        c = up(bus, w)
        c.signal("PrepareForSleep", True)
        until(lambda: c.lock_released(0))
        c.signal("PrepareForSleep", False)
        until(lambda: hooks.events == ["sleep", "wake"])
        assert w.connected
    finally:
        w.stop()


def test_a_stuck_engine_call_never_holds_the_suspend_longer_than_the_budget(bus, hooks):
    hooks.block = threading.Event()
    w = watcher(bus, hooks, budget_s=0.3)
    try:
        c = up(bus, w)
        t0 = time.monotonic()
        c.signal("PrepareForSleep", True)
        until(lambda: c.lock_released(0), what="lock released although the call is stuck")
        assert time.monotonic() - t0 < 1.5 and hooks.events == ["sleep"]
        c.signal("PrepareForSleep", False)           # the watcher is not stuck behind the callback
        until(lambda: hooks.events == ["sleep", "wake"] and len(c.locks) == 2)
    finally:
        hooks.block.set()
        w.stop()


def test_without_permission_for_the_lock_the_hint_still_fires(bus, hooks):
    bus.inhibit_error = "org.freedesktop.DBus.Error.AccessDenied"
    w = watcher(bus, hooks)
    try:
        c = bus.wait_conn()
        until(lambda: w.connected)
        assert not w.inhibited
        c.signal("PrepareForSleep", True)
        until(lambda: hooks.events == ["sleep"])
        c.signal("PrepareForSleep", False)
        until(lambda: hooks.events == ["sleep", "wake"])
    finally:
        w.stop()


def test_lock_reply_without_an_fd_is_not_fatal(bus, hooks):
    bus.inhibit_without_fd = True
    w = watcher(bus, hooks)
    try:
        c = bus.wait_conn()
        until(lambda: w.connected)
        c.signal("PrepareForSleep", True)
        until(lambda: hooks.events == ["sleep"])
        assert not w.inhibited
    finally:
        w.stop()


def test_bus_gone_releases_the_lock_then_reconnects_and_rearms(bus, hooks):
    w = watcher(bus, hooks)
    try:
        c1 = up(bus, w)
        c1.close()
        until(lambda: c1.lock_released(0), what="lock released when the bus went away")
        c2 = up(bus, w, n=2)
        assert bus.matches[-2:] == [SIGNAL_RULE % "PrepareForSleep", SIGNAL_RULE % "PrepareForShutdown"] and len(bus.matches) == 4
        c2.signal("PrepareForSleep", True)
        until(lambda: hooks.events == ["sleep"])
    finally:
        w.stop()


def test_bus_unreachable_then_available(tmp_path, hooks):
    path = "\0inkterface-late-%d" % os.getpid()
    w = SleepWatcher(hooks.on_sleep, hooks.on_wake, bus_factory=lambda: D.Bus(path, uid=1000, timeout=1.0),
                     retry_min_s=0.05, retry_max_s=0.1)
    w.start()
    try:
        until(lambda: w.last_error is not None, what="error recorded")
        assert not w.connected and not w.inhibited and "Error" in w.last_error
        b = FakeBus(path)
        try:
            until(lambda: w.connected, what="connected once the bus appears")
            assert w.last_error is None
        finally:
            b.stop()
    finally:
        w.stop()


def test_refused_authentication_keeps_retrying_quietly(tmp_path, hooks):
    b = FakeBus(auth_reply=b"REJECTED EXTERNAL\r\n")
    w = SleepWatcher(hooks.on_sleep, hooks.on_wake, bus_factory=lambda: D.Bus(b.path, uid=1000, timeout=1.0),
                     retry_min_s=0.02, retry_max_s=0.05)
    w.start()
    try:
        until(lambda: len(b.conns) >= 3, what="retries")
        assert "authentication refused" in w.last_error and not w.connected and hooks.events == []
    finally:
        w.stop()
        b.stop()


def test_stop_releases_the_lock_and_returns_promptly_and_start_is_idempotent(bus, hooks):
    w = watcher(bus, hooks)
    c = up(bus, w)
    w.start()
    assert len([t for t in threading.enumerate() if t.name == "inkterface-sleep-hint"]) == 1
    t0 = time.monotonic()
    w.stop()
    assert time.monotonic() - t0 < 2
    until(lambda: c.lock_released(0))
    assert not w.inhibited and not w.connected
    w.stop()
    assert not [t for t in threading.enumerate() if t.name == "inkterface-sleep-hint"]


def test_stop_while_waiting_to_retry_is_fast(tmp_path, hooks):
    w = SleepWatcher(hooks.on_sleep, hooks.on_wake, bus_factory=lambda: D.Bus("\0inkterface-nobody-home", uid=1000),
                     retry_min_s=30, retry_max_s=30)
    w.start()
    until(lambda: w.last_error is not None)
    t0 = time.monotonic()
    w.stop()
    assert time.monotonic() - t0 < 2


def test_two_sleeps_in_a_row_tell_the_engine_twice_and_do_not_leak_fds(bus, hooks):
    w = watcher(bus, hooks)
    try:
        c = up(bus, w)
        base = open_fds()
        for _ in range(3):
            c.signal("PrepareForSleep", True)
            c.signal("PrepareForSleep", True)
            c.signal("PrepareForSleep", False)
        until(lambda: hooks.events.count("wake") == 3)
        assert hooks.events.count("sleep") == 6
        time.sleep(0.2)
        assert open_fds() <= base + 3, "a client fd leaked"   # the fake bus keeps one read end per re-taken lock
    finally:
        w.stop()


# =============================================================================== Engine over the emulator
def rig(tmp_path, **kw):
    holder = {}

    def sleep(dt):   # the engine's wait loop: time only moves when it waits
        holder["r"].clock.t += dt

    r = holder["r"] = Rig(tmp_path, sleep=sleep, **kw)
    r.run(2)
    assert r.st["state"] == "ready" and r.panel.mode == P.MODE_HOST
    return r


def test_prepare_sleep_puts_the_panel_on_idle_and_the_engine_goes_quiet(tmp_path):
    r = rig(tmp_path)
    n = len(r.link.written)
    t0 = r.clock.t
    assert r.engine.prepare_sleep() is True
    assert r.panel.mode == P.MODE_PANEL and r.clock.t - t0 < 0.1          # ACK came at once
    assert [f.type for f in r.link.written[n:]] == [P.T_MODE] and r.link.written[-1].payload == bytes([P.MODE_PANEL])
    assert r.st["asleep"] is True and r.st["connected"]
    n = len(r.link.written)
    r.data.change(**{"system__cpu__load": 99.0})
    r.run(15)        # no heartbeat, no frame, no MODE HOST that would pull the panel out of idle (the failsafe is at 20 s)
    assert len(r.link.written) == n and r.panel.mode == P.MODE_PANEL and r.st["state"] == "ready"
    assert r.engine.prepare_sleep() is True and len(r.link.written) == n    # idempotent


def test_panel_log_lines_are_still_read_while_asleep_and_a_dead_port_is_not_reopened(tmp_path, caplog):
    import logging
    caplog.set_level(logging.INFO)
    r = rig(tmp_path)
    r.engine.prepare_sleep()
    r.link._out.append((r.clock.t, P.encode(P.T_LOG, 0, b"[1] idle full refresh 3995 ms\n")))   # the panel reports its idle draw
    r.run(0.2)
    assert "panel: [1] idle full refresh 3995 ms" in caplog.text
    r.link.unplug()
    r.run(5)
    assert r.st["state"] == "disconnected" and len(r.links) == 1 and r.st["asleep"]
    r.engine.wake()
    r.run(0.5)
    assert r.st["state"] == "ready" and len(r.links) == 2


def test_wake_gives_the_display_back_with_a_full_redraw_right_away(tmp_path):
    r = rig(tmp_path)
    r.engine.prepare_sleep()
    n, t_wake = len(r.link.written), r.clock.t
    r.engine.wake()
    assert r.st["asleep"] is False
    r.run(1)
    new = r.link.written[n:]
    assert new[0].type == P.T_MODE and new[0].payload[0] == P.MODE_HOST and r.panel.mode == P.MODE_HOST
    refreshes = [f for f in new if f.type == P.T_REFRESH]
    assert refreshes and refreshes[0].payload == b"\x01"                  # a FULL refresh: the glass shows the idle picture
    assert len([f for f in new if f.type == P.T_RECT]) == 10              # the whole frame again, nothing assumed
    assert r.st["full_refreshes"] >= 2 and r.st["last_error"] is None
    assert r.clock.t - t_wake <= 1.0    # the emulator is instant: this is the engine's own delay (one step)
    r.engine.wake()   # no-op when awake


def test_wake_resumes_normal_updates(tmp_path):
    r = rig(tmp_path)
    r.engine.prepare_sleep()
    r.engine.wake()
    r.run(2)
    before = r.st["frames_rendered"]
    r.data.change(**{"system__cpu__load": 77.0})
    r.run(2)
    assert r.st["frames_rendered"] > before and r.st["heartbeats_sent"] >= 0
    assert r.panel.mode == P.MODE_HOST


def test_prepare_sleep_with_the_panel_unplugged_returns_false_at_once_and_wake_reconnects_without_backoff(tmp_path):
    r = rig(tmp_path)
    r.link.unplug()
    t0 = time.monotonic()
    assert r.engine.prepare_sleep() is False
    assert time.monotonic() - t0 < 0.5      # no waiting for an ACK that cannot come
    assert r.st["asleep"] is True
    r.run(15)
    assert len(r.links) == 1                 # no reconnect attempts that would pull the panel back while asleep
    r.engine.wake()
    r.run(1)
    assert len(r.links) == 2 and r.st["state"] == "ready" and r.panel.mode == P.MODE_HOST


def test_prepare_sleep_when_the_panel_is_not_connected(tmp_path):
    r = Rig(tmp_path)
    assert r.st["state"] == "disconnected"
    assert r.engine.prepare_sleep() is False
    r.run(10)
    assert not r.links                       # asleep: no connection attempts
    r.engine.wake()
    r.run(2)
    assert r.st["state"] == "ready"


def test_ack_timeout_returns_false_after_the_budget_and_never_longer(tmp_path):
    r = rig(tmp_path)
    r.link.mute = True
    t0 = r.clock.t
    assert r.engine.prepare_sleep(timeout=3.0) is False
    assert 2.9 <= r.clock.t - t0 <= 3.2      # waited the budget on the fake clock, not more
    assert r.st["asleep"] is True


def test_mode_ack_that_waits_for_a_refresh_in_flight(tmp_path):
    r = rig(tmp_path, full_s=2.0, partial_s=0.7)
    r.data.change(**{"system__cpu__load": 55.0})
    r.run(0.2)
    assert any(i[1] == "refresh" for i in r.engine._inflight.values())      # a refresh is on the glass
    t0 = r.clock.t
    assert r.engine.prepare_sleep(timeout=3.0) is True
    assert 0.1 < r.clock.t - t0 < 1.0 and r.panel.mode == P.MODE_PANEL     # waited for it, as the real panel does
    r2 = rig(tmp_path / "x", full_s=10.0)
    r2.engine._inflight.clear()
    r2.engine._link.write(P.refresh(200, True), 1.0)                        # a 10 s full refresh in flight
    r2.engine._inflight[200] = (9, "refresh", r2.clock.t)
    assert r2.engine.prepare_sleep(timeout=3.0) is False                    # longer than the budget: give up, let it sleep


def test_nak_for_mode_panel_counts_as_failure(tmp_path):
    r = rig(tmp_path)
    r.panel.idle_timeout_s = 60
    real = r.panel._mode
    r.panel._mode = lambda f: r.panel._nak(f.seq, P.NAK_BAD_ARG)
    assert r.engine.prepare_sleep() is False
    r.panel._mode = real


def test_a_dead_link_during_the_ack_wait_is_not_fatal(tmp_path):
    r = rig(tmp_path)
    r.link.fail_after = 3          # the port dies in the middle of the MODE frame
    assert r.engine.prepare_sleep() is False
    assert r.st["last_error"] and r.st["asleep"]
    r.engine.wake()
    r.run(3)
    assert r.st["state"] == "ready"


def test_a_suspend_that_never_happens_wakes_the_engine_by_itself(tmp_path):
    r = rig(tmp_path)
    r.engine.prepare_sleep()
    r.run(E.WAKE_FAILSAFE_S - 1)
    assert r.st["asleep"] is True and r.panel.mode == P.MODE_PANEL
    r.run(3)
    assert r.st["asleep"] is False and r.panel.mode == P.MODE_HOST and r.st["full_refreshes"] >= 2


def test_pause_and_stop_states_refuse_prepare_sleep(tmp_path):
    r = rig(tmp_path)
    r.engine.pause()
    assert r.engine.prepare_sleep() is False and r.st["asleep"] is False and r.st["state"] == "paused"
    r.engine.resume()
    r.run(2)
    r.engine.stop()
    assert r.engine.prepare_sleep() is False


def test_stop_waits_for_the_ack_of_mode_panel_and_skips_it_when_already_asleep(tmp_path):
    r = rig(tmp_path)
    r.engine._shutdown()
    assert r.panel.mode == P.MODE_PANEL and r.st["state"] == "stopped"
    r2 = rig(tmp_path / "b")
    r2.engine.prepare_sleep()
    n = len(r2.link.written)
    r2.engine._shutdown()
    assert len(r2.link.written) == n and r2.st["asleep"] is False and r2.st["state"] == "stopped"
    r3 = rig(tmp_path / "c")
    r3.link.mute = True
    t0 = time.monotonic()
    r3.engine._shutdown()                      # a hung panel must not hang the unload
    assert time.monotonic() - t0 < 3 and r3.st["state"] == "stopped"


def test_an_interrupted_idle_store_upload_is_sent_again_after_wake(tmp_path):
    from test_engine_idle import plan

    from inkterface_core.emulator import EmulatedPanel

    caps = P.CAP_GRAY4 | P.CAP_IDLE_STORAGE
    holder = {}
    r = holder["r"] = Rig(tmp_path, panel=EmulatedPanel(caps=caps, idle_free=49152), full_s=0.0,
                          sleep=lambda dt: holder["r"].clock.__setattr__("t", holder["r"].clock.t + dt))
    p = plan("a", "b")
    r.engine.set_idle_plan(p)
    for _ in range(100):                          # until the upload is under way
        r.run(0.05)
        if r.st["idle"]["state"] == "uploading":
            break
    assert r.st["idle"]["state"] == "uploading" and r.panel.idle_store is None
    r.engine.prepare_sleep()
    r.engine.wake()
    r.run(5)
    assert r.panel.idle_store == p.blob and r.st["idle"]["state"] == "ok" and r.st["naks"] == 0


# =============================================================================== everything together, real threads and clock
def test_logind_signal_to_panel_and_back_end_to_end(bus, tmp_path):
    """fake logind -> dbus_min -> SleepWatcher -> Engine thread -> emulated panel, real clock, real threads."""
    from engine_testlib import Data, make_screens

    from inkterface_core.emulator import EmulatedPanel
    from inkterface_core.engine import EmulatedLink, Engine, Settings

    panel = EmulatedPanel()
    eng = Engine(Data(), make_screens(tmp_path / "s"), Settings(rotation=["a"]), lambda: EmulatedLink(panel))
    eng.start()
    w = SleepWatcher(eng.prepare_sleep, eng.wake, bus_factory=lambda: D.Bus(bus.path, uid=1000, timeout=2.0))
    w.start()
    try:
        until(lambda: eng.status()["full_refreshes"] >= 1 and panel.mode == P.MODE_HOST, what="panel showing the active screen")
        c = up(bus, w)
        t0 = time.monotonic()
        c.signal("PrepareForSleep", True)
        until(lambda: panel.mode == P.MODE_PANEL, what="panel on idle")
        until(lambda: c.lock_released(0), what="lock released")
        assert time.monotonic() - t0 < 1.0 and eng.status()["asleep"]
        time.sleep(0.3)
        assert panel.mode == P.MODE_PANEL     # nothing pulled it back
        full = eng.status()["full_refreshes"]
        c.signal("PrepareForSleep", False)
        until(lambda: panel.mode == P.MODE_HOST and eng.status()["full_refreshes"] > full, what="active screen back")
        until(lambda: len(c.locks) == 2, what="lock re-armed")
        assert eng.status()["naks"] == 0 and not eng.status()["asleep"]
    finally:
        w.stop()
        eng.stop()
    assert panel.mode == P.MODE_PANEL         # stop() still hands the panel to its idle screens


def test_prepare_sleep_from_another_thread_while_the_engine_runs_and_frames_are_queued(tmp_path):
    from engine_testlib import Data, make_screens

    from inkterface_core.emulator import EmulatedPanel
    from inkterface_core.engine import EmulatedLink, Engine, Settings

    panel, data = EmulatedPanel(), Data()
    eng = Engine(data, make_screens(tmp_path / "s"), Settings(rotation=["a"]), lambda: EmulatedLink(panel, partial_s=0.05, full_s=0.2))
    eng.start()
    try:
        until(lambda: eng.status()["full_refreshes"] >= 1)
        for i in range(20):                  # a stream of changes keeps frames in flight
            data.change(**{"system__cpu__load": float(i)})
            time.sleep(0.01)
        assert eng.prepare_sleep(timeout=3.0) is True and panel.mode == P.MODE_PANEL
        n = eng.status()["bytes_sent"]
        time.sleep(0.4)
        assert eng.status()["bytes_sent"] == n and panel.mode == P.MODE_PANEL
        eng.wake()
        until(lambda: panel.mode == P.MODE_HOST)
        assert eng.status()["naks"] == 0
    finally:
        eng.stop()


# =============================================================================== the real bus (skipped where there is none)
def real_bus_available():
    if not os.path.exists(D.SYSTEM_BUS):
        return False
    try:
        b = D.Bus(timeout=2.0)
        b.connect()
        b.close()
        return True
    except (OSError, D.BusError):
        return False


@pytest.mark.skipif(not real_bus_available(), reason="no system D-Bus here")
def test_real_system_bus_handshake_match_and_delay_lock():
    """Cross-checks the encoder/decoder against the real dbus-daemon and logind. Takes a delay lock for a moment."""
    b = D.Bus(timeout=5.0)
    b.connect()
    try:
        assert b.name.startswith(":")
        owner = b.call("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "GetNameOwner", "s",
                       ("org.freedesktop.login1",))[0][0]
        assert owner.startswith(":")
        b.add_match(SIGNAL_RULE % "PrepareForSleep")
        try:
            fd = b.inhibit("sleep:shutdown", "inkterface-test", "unit test, ignore", "delay")
        except D.BusError as e:
            # CI runners have a system bus but no seat: logind's polkit refuses delay locks there.
            if "AccessDenied" in str(e):
                pytest.skip(f"logind refuses a delay lock here: {e}")
            raise
        out = subprocess.run(["systemd-inhibit", "--list"], capture_output=True, text=True).stdout
        assert "inkterface-test" in out
        os.close(fd)
        time.sleep(0.2)
        assert "inkterface-test" not in subprocess.run(["systemd-inhibit", "--list"], capture_output=True, text=True).stdout
        with pytest.raises(D.BusError):
            b.call("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "NoSuchMethod")
    finally:
        b.close()


# =============================================================================== firmware (host compiled)
FW = Path(__file__).resolve().parents[2] / "firmware"


@pytest.mark.skipif(shutil.which("g++") is None, reason="g++ not available")
def test_firmware_bus_watch_host_tests(tmp_path):
    exe = tmp_path / "usb_bus_watch_host"
    subprocess.run(["g++", "-std=c++17", "-O1", "-g", "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined",
                    f"-I{FW}/src/ee05", f"{FW}/host_test/usb_bus_watch_host.cpp", f"{FW}/src/ee05/usbproto.cpp", "-o", str(exe)], check=True)
    r = subprocess.run([str(exe)], capture_output=True, text=True)
    assert r.returncode == 0 and "usb_bus_watch host tests OK" in r.stdout, r.stdout + r.stderr


@pytest.mark.skipif(shutil.which("g++") is None, reason="g++ not available")
def test_firmware_override_of_the_down_time_compiles_and_still_passes(tmp_path):
    """A bench build may override the down time (-D INK_BUS_DOWN_MS): the detector must behave the same at other values."""
    for ms in (2500, 5000):
        exe = tmp_path / f"w{ms}"
        subprocess.run(["g++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror", f"-DINK_BUS_DOWN_MS={ms}",
                        f"-I{FW}/src/ee05", f"{FW}/host_test/usb_bus_watch_host.cpp", f"{FW}/src/ee05/usbproto.cpp", "-o", str(exe)], check=True)
        assert subprocess.run([str(exe)], capture_output=True, text=True).returncode == 0


def test_the_firmware_default_is_within_the_requirement():
    src = (FW / "src/ee05/usb_bus_watch.h").read_text()
    assert "#define INK_BUS_DOWN_MS 2000" in src and "main.cpp" not in src
    main = (FW / "src/ee05/main.cpp").read_text()
    assert "USB.extBusy(now)" in main and "Serial.isPlugged()" in main and "BUS_WATCH.reset()" in main


def test_no_new_protocol_value_is_needed():
    """MODE PANEL (1) already means 'idle now, stay until MODE HOST'; the sleep hint uses exactly that."""
    assert (P.MODE_HOST, P.MODE_PANEL) == (0, 1)
