import errno
import os
import sys

import pytest

from inkterface_core import proto as P
from inkterface_core import serialport as sp
from inkterface_core import usb_link


def make_sysfs(root, tty, vid, serial_number=None):
    """Minimal /sys/class/tty/<tty>/device -> usb interface dir under a usb device dir holding idVendor."""
    usb = root / "devices" / f"usb-{tty}"
    iface = usb / "1-1:1.0"
    iface.mkdir(parents=True)
    (usb / "idVendor").write_text(f"{vid:04x}\n")
    if serial_number is not None:
        (usb / "serial").write_text(serial_number + "\n")
    cls = root / "class" / "tty" / tty
    cls.mkdir(parents=True, exist_ok=True)
    (cls / "device").symlink_to(iface)


@pytest.fixture
def sysfs(tmp_path):
    make_sysfs(tmp_path, "ttyACM1", 0x28DE, "puck")  # Steam Controller puck: must never match
    make_sysfs(tmp_path, "ttyACM2", 0x303A, "AA:BB:CC:DD:EE:FF")
    make_sysfs(tmp_path, "ttyACM0", 0x1532)  # Razer
    make_sysfs(tmp_path, "ttyACM10", 0x303A, "AA:BB")
    (tmp_path / "class" / "tty" / "ttyS0").mkdir()  # no USB parent
    (tmp_path / "class" / "tty" / "tty1").mkdir()
    return str(tmp_path / "class" / "tty")


def test_find_panels_matches_only_espressif_vid(sysfs):
    assert usb_link.find_panels(sys_tty=sysfs) == ["/dev/ttyACM2", "/dev/ttyACM10"]


def test_find_panels_serial_filter_case_insensitive(sysfs):
    assert usb_link.find_panels("aa:bb:cc", sys_tty=sysfs) == ["/dev/ttyACM2"]
    assert usb_link.find_panels("nomatch", sys_tty=sysfs) == []


def test_find_panels_none_present(tmp_path):
    assert usb_link.find_panels(sys_tty=str(tmp_path / "missing")) == []
    (tmp_path / "empty").mkdir()
    assert usb_link.find_panels(sys_tty=str(tmp_path / "empty")) == []


def test_find_panels_ignores_garbage_idvendor(tmp_path):
    make_sysfs(tmp_path, "ttyACM0", 0x303A)
    (tmp_path / "devices" / "usb-ttyACM0" / "idVendor").write_text("zzzz")
    assert usb_link.find_panels(sys_tty=str(tmp_path / "class" / "tty")) == []


def test_is_espressif_resolves_symlink(sysfs, tmp_path):
    link = tmp_path / "by-id"
    link.symlink_to("/dev/ttyACM2")
    assert usb_link.is_espressif(str(link), sysfs)
    assert usb_link.is_espressif("/dev/ttyACM10", sysfs)
    assert not usb_link.is_espressif("/dev/ttyACM1", sysfs)
    assert not usb_link.is_espressif("/dev/ttyACM0", sysfs)
    assert not usb_link.is_espressif("/dev/ttyS0", sysfs)
    assert not usb_link.is_espressif("/dev/does-not-exist", sysfs)


def test_open_port_never_exposes_dtr0_rts1(monkeypatch):
    """Chip resets on (DTR=0, RTS=1). Replay every modem ioctl from the kernel's open state (1,1)."""
    master, slave = os.openpty()
    state, seen = {"dtr": True, "rts": True}, []
    real = sp.fcntl.ioctl

    def spy(fd, op, arg=0, *a):
        if op in (sp.TIOCMBIS, sp.TIOCMBIC):
            bits = int.from_bytes(arg, sys.byteorder)
            for name, bit in (("dtr", sp.TIOCM_DTR), ("rts", sp.TIOCM_RTS)):
                if bits & bit:
                    state[name] = op == sp.TIOCMBIS
            seen.append((state["dtr"], state["rts"]))
            raise OSError(errno.ENOTTY, "pty has no modem lines")
        return real(fd, op, arg, *a)

    monkeypatch.setattr(sp.fcntl, "ioctl", spy)
    try:
        usb_link.open_port(os.ttyname(slave)).close()
    finally:
        os.close(master), os.close(slave)
    assert seen and (False, True) not in seen
    assert seen[-1] == (False, False)


class Wire:
    """Serial-like panel stub: ACKs (or NAKs) each frame it is written, tracks bytes in flight."""

    def __init__(self, nak_seqs=(), silent=False):
        self.rx = bytearray()
        self.nak_seqs, self.silent = set(nak_seqs), silent
        self.written = []
        self.asked = []

    def write(self, b, timeout=None):
        self.written.append(bytes(b))
        for f in P.Decoder().feed(bytes(b)):
            if self.silent:
                continue
            if f.seq in self.nak_seqs:
                self.rx += P.encode(P.T_NAK, 0, bytes([f.seq, P.NAK_BAD_RECT]))
            else:
                self.rx += P.encode(P.T_ACK, 0, bytes([f.seq]))

    def read(self, n, timeout=None):
        self.asked.append((n, timeout))
        out, self.rx = bytes(self.rx[:n]), self.rx[n:]
        return out


def test_next_seq_wraps():
    l = usb_link.Link(Wire())
    l.seq = 254
    assert [l.next_seq() for _ in range(4)] == [254, 255, 0, 1]


def test_poll_routes_log_frames_and_returns_others():
    w = Wire()
    logs = []
    l = usb_link.Link(w, log=logs.append)
    w.rx += P.encode(P.T_LOG, 0, b"hello log\r\n") + P.encode(P.T_ACK, 1, b"\x05")
    frames = l.poll()
    assert logs == ["hello log"]
    assert [(f.type, f.payload) for f in frames] == [(P.T_ACK, b"\x05")]


def test_poll_read_never_blocks_unbounded():
    w = Wire()
    usb_link.Link(w).poll(0.2)
    assert w.asked and all(t is not None and 0 <= t <= 0.05 for _, t in w.asked)


def test_poll_times_out_empty():
    assert usb_link.Link(Wire()).poll(0.05) == []


def test_poll_survives_garbage_and_split_frames():
    w = Wire()
    l = usb_link.Link(w)
    frame = P.encode(P.T_ACK, 1, b"\x09")
    w.rx += b"junk" + frame[:5]
    assert l.poll() == []
    w.rx += frame[5:]
    assert [f.payload for f in l.poll()] == [b"\x09"]


def items(n, size):
    return [(i, P.encode(P.T_RECT, i, bytes(size))) for i in range(n)]


def test_send_acked_all_acked():
    w = Wire()
    ok, naks = usb_link.Link(w).send_acked(items(10, 1000), window=8192)
    assert ok and naks == [] and len(w.written) == 10


def test_send_acked_respects_window():
    w = Wire()
    l = usb_link.Link(w)
    # frame wire size = 1010; window 2500 allows 2 in flight, never 3
    inflight, peak = set(), [0]
    orig_write = w.write

    def write(b, timeout=None):
        for f in P.Decoder().feed(bytes(b)):
            inflight.add(f.seq)
        peak[0] = max(peak[0], len(inflight))
        orig_write(b)

    w.write = write
    orig_read = w.read

    def read(n, timeout=None):
        out = orig_read(n, timeout)
        for f in P.Decoder().feed(out):
            inflight.discard(f.payload[0])
        return out

    w.read = read
    ok, _ = l.send_acked(items(9, 1000), window=2500)
    assert ok and peak[0] == 2


def test_send_acked_oversize_single_frame_still_sent():
    w = Wire()
    ok, _ = usb_link.Link(w).send_acked(items(1, 4000), window=100)
    assert ok and len(w.written) == 1


def test_send_acked_reports_naks():
    w = Wire(nak_seqs={2})
    ok, naks = usb_link.Link(w).send_acked(items(4, 10), window=8192)
    assert not ok and naks == [(2, P.NAK_BAD_RECT)]


def test_send_acked_times_out_when_panel_silent():
    ok, naks = usb_link.Link(Wire(silent=True)).send_acked(items(2, 10), window=8192, timeout=0.1)
    assert not ok and naks == []


def test_send_acked_empty():
    assert usb_link.Link(Wire()).send_acked([], window=8192) == (True, [])
