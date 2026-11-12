"""SerialPort against a pty pair (os.openpty): the kernel tty driver is real, only the USB device is faked."""
import errno
import fcntl
import os
import struct
import termios  # tests only: independent oracle for our hand-rolled ioctl constants/layout
import threading
import time

import pytest

from inkterface_core import SerialPort, serialport as sp

NASTY = bytes([0x00, 0x0A, 0x0D, 0x11, 0x13, 0x03, 0x1A, 0x7F, 0xFF, 0x0D, 0x0A])  # NUL LF CR XON XOFF ^C ^Z DEL 0xFF CRLF
ALL = bytes(range(256))


@pytest.fixture
def pty_pair():
    master, slave = os.openpty()
    os.set_blocking(master, False)
    path = os.ttyname(slave)
    yield master, slave, path
    for fd in (master, slave):
        try:
            os.close(fd)
        except OSError:
            pass


@pytest.fixture
def port(pty_pair):
    p = SerialPort.open(pty_pair[2])
    yield p
    p.close()


def read_master(master, n, timeout=2.0):
    end, out = time.time() + timeout, b""
    while len(out) < n and time.time() < end:
        try:
            out += os.read(master, n - len(out))
        except BlockingIOError:
            time.sleep(0.005)
    return out


# --- constants / layout vs the stdlib termios oracle ---------------------------------------------

def test_constants_match_termios():
    assert (sp.TCGETS, sp.TCSETS, sp.TIOCMBIS, sp.TIOCMBIC) == (termios.TCGETS, termios.TCSETS, termios.TIOCMBIS, termios.TIOCMBIC)
    assert (sp.TIOCM_DTR, sp.TIOCM_RTS) == (termios.TIOCM_DTR, termios.TIOCM_RTS)
    assert (sp.TCFLSH, sp.TCIFLUSH) == (termios.TCFLSH, termios.TCIFLUSH)
    for n in "IGNBRK BRKINT PARMRK ISTRIP INLCR IGNCR ICRNL IXON IXANY IXOFF CSIZE CS8 PARENB CREAD CLOCAL VTIME VMIN".split():
        assert getattr(sp, n) == getattr(termios, n), n


def test_struct_size_is_kernel_termios(pty_pair):
    assert sp._SIZE == 36
    assert len(fcntl.ioctl(pty_pair[1], sp.TCGETS, bytes(sp._SIZE))) == 36


def dirty(fd):
    """Put the tty in the worst case: every flag we must clear is set, 7-bit with parity."""
    iflag, oflag, cflag, lflag, ispeed, ospeed, cc = termios.tcgetattr(fd)
    iflag |= termios.IGNBRK | termios.BRKINT | termios.PARMRK | termios.ISTRIP | termios.INLCR | termios.IGNCR | termios.ICRNL | termios.IXON | termios.IXOFF | termios.IXANY
    oflag |= termios.OPOST
    cflag = (cflag & ~termios.CSIZE) | termios.CS7 | termios.PARENB
    lflag |= termios.ECHO | termios.ECHONL | termios.ICANON | termios.ISIG | termios.IEXTEN
    cc[termios.VMIN], cc[termios.VTIME] = 7, 9
    termios.tcsetattr(fd, termios.TCSANOW, [iflag, oflag, cflag, lflag, ispeed, ospeed, cc])


def test_open_sets_raw_mode(pty_pair):
    dirty(pty_pair[1])
    with SerialPort.open(pty_pair[2]):
        iflag, oflag, cflag, lflag, _ispeed, _ospeed, cc = termios.tcgetattr(pty_pair[1])
    assert not iflag & (termios.IGNBRK | termios.BRKINT | termios.PARMRK | termios.ISTRIP | termios.INLCR | termios.IGNCR | termios.ICRNL | termios.IXON | termios.IXOFF | termios.IXANY)
    assert oflag == 0
    assert lflag == 0
    assert cflag & termios.CSIZE == termios.CS8 and cflag & termios.CLOCAL and cflag & termios.CREAD and not cflag & termios.PARENB
    assert cc[termios.VMIN] == 0 and cc[termios.VTIME] == 0


def test_bytes_survive_a_dirty_tty(pty_pair):
    dirty(pty_pair[1])
    with SerialPort.open(pty_pair[2]) as p:
        os.write(pty_pair[0], ALL)
        got = b""
        while len(got) < 256:
            chunk = p.read(256, timeout=2)
            assert chunk
            got += chunk
        assert got == ALL
        p.write(ALL)
        assert read_master(pty_pair[0], 256) == ALL


# --- raw byte round trip -------------------------------------------------------------------------

@pytest.mark.parametrize("data", [NASTY, ALL, b"\x00", b"\x11", b"\x13", b"\r", b"\n", b"\r\n", b"\x03"])
def test_write_reaches_peer_unaltered(pty_pair, port, data):
    port.write(data)
    assert read_master(pty_pair[0], len(data)) == data


@pytest.mark.parametrize("data", [NASTY, ALL, b"\x00", b"\x11", b"\x13", b"\r", b"\n", b"\r\n", b"\x03"])
def test_read_gets_peer_bytes_unaltered(pty_pair, port, data):
    os.write(pty_pair[0], data)
    got = b""
    while len(got) < len(data):
        chunk = port.read(len(data) - len(got), timeout=2)
        assert chunk, f"timeout after {got!r}"
        got += chunk
    assert got == data


def test_control_group_without_raw_mode_is_altered(pty_pair):
    """Negative control: a plain os.open() pty is cooked, so the tests above really prove raw mode."""
    fd = os.open(pty_pair[2], os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        os.write(pty_pair[0], b"a\r")
        time.sleep(0.1)
        assert os.read(fd, 16) != b"a\r"  # ICRNL turns CR into LF (and canonical mode may hold it back)
    finally:
        os.close(fd)


def test_large_write_exercises_backpressure(pty_pair, port):
    payload = os.urandom(300_000)
    got = bytearray()
    done = threading.Event()

    def drain():
        while len(got) < len(payload):
            try:
                got.extend(os.read(pty_pair[0], 65536))
            except BlockingIOError:
                time.sleep(0.001)
        done.set()

    os.set_blocking(pty_pair[0], False)
    t = threading.Thread(target=drain, daemon=True)
    t.start()
    port.write(payload, timeout=5)
    assert done.wait(5) and bytes(got) == payload


def test_write_times_out_when_peer_never_reads(pty_pair, port):
    with pytest.raises(TimeoutError):
        port.write(b"x" * 5_000_000, timeout=0.2)


def test_write_empty_is_noop(pty_pair, port):
    port.write(b"")
    assert read_master(pty_pair[0], 1, timeout=0.1) == b""


# --- read semantics ------------------------------------------------------------------------------

def test_read_timeout_returns_empty(port):
    t = time.time()
    assert port.read(10, timeout=0.05) == b""
    assert 0.04 <= time.time() - t < 1


def test_read_zero_or_negative_size(port):
    assert port.read(0) == b"" and port.read(-5) == b""


def test_read_returns_partial_up_to_size(pty_pair, port):
    os.write(pty_pair[0], b"abcdef")
    time.sleep(0.05)
    assert port.read(4, timeout=1) == b"abcd" and port.read(10, timeout=1) == b"ef"


def test_read_after_peer_hangup_raises_eio(pty_pair, port):
    os.close(pty_pair[0])
    with pytest.raises(OSError) as e:
        port.read(10, timeout=1)
    assert e.value.errno == errno.EIO


# --- lifecycle / failure -------------------------------------------------------------------------

def test_close_idempotent_and_use_after_close(pty_pair):
    p = SerialPort.open(pty_pair[2])
    p.close()
    p.close()
    with pytest.raises(ValueError):
        p.read(1)
    with pytest.raises(ValueError):
        p.write(b"x")


def test_context_manager_closes(pty_pair):
    with SerialPort.open(pty_pair[2]) as p:
        fd = p._fd
    assert p._fd is None
    with pytest.raises(OSError):
        os.fstat(fd)


def test_open_missing_path():
    with pytest.raises(FileNotFoundError):
        SerialPort.open("/dev/ttyACM_does_not_exist")


def test_open_non_tty_fails_and_leaks_no_fd(tmp_path):
    f = tmp_path / "plain"
    f.write_bytes(b"")
    before = set(os.listdir("/proc/self/fd"))
    with pytest.raises(OSError) as e:
        SerialPort.open(str(f))
    assert e.value.errno == errno.ENOTTY
    assert set(os.listdir("/proc/self/fd")) <= before | {str(i) for i in range(0)}


def test_open_dev_null_rejected():
    with pytest.raises(OSError) as e:
        SerialPort.open("/dev/null")
    assert e.value.errno == errno.ENOTTY


# --- DTR/RTS -------------------------------------------------------------------------------------

@pytest.fixture
def ioctls(monkeypatch):
    calls = []
    real = fcntl.ioctl

    def spy(fd, op, arg=0, *a):
        if op in (sp.TIOCMBIS, sp.TIOCMBIC):
            calls.append((op, struct.unpack("I", arg)[0]))
            raise OSError(errno.ENOTTY, "pty has no modem lines")  # what a pty answers
        return real(fd, op, arg, *a)

    monkeypatch.setattr(sp.fcntl, "ioctl", spy)
    return calls


def test_default_lowers_dtr_and_rts(pty_pair, ioctls):
    SerialPort.open(pty_pair[2]).close()
    assert ioctls == [(sp.TIOCMBIC, sp.TIOCM_DTR | sp.TIOCM_RTS)]


def test_dtr_high_rts_low(pty_pair, ioctls):
    SerialPort.open(pty_pair[2], dtr=True).close()
    assert ioctls == [(sp.TIOCMBIS, sp.TIOCM_DTR), (sp.TIOCMBIC, sp.TIOCM_RTS)]


def test_both_high_only_sets(pty_pair, ioctls):
    SerialPort.open(pty_pair[2], dtr=True, rts=True).close()
    assert ioctls == [(sp.TIOCMBIS, sp.TIOCM_DTR | sp.TIOCM_RTS)]


def test_real_modem_error_is_not_swallowed(pty_pair, monkeypatch):
    real = fcntl.ioctl

    def spy(fd, op, arg=0, *a):
        if op == sp.TIOCMBIC:
            raise OSError(errno.EIO, "device gone")
        return real(fd, op, arg, *a)

    monkeypatch.setattr(sp.fcntl, "ioctl", spy)
    before = set(os.listdir("/proc/self/fd"))
    with pytest.raises(OSError) as e:
        SerialPort.open(pty_pair[2])
    assert e.value.errno == errno.EIO
    assert set(os.listdir("/proc/self/fd")) == before  # fd closed on failed open


def test_non_linux_refused(monkeypatch, pty_pair):
    monkeypatch.setattr(sp.sys, "platform", "darwin")
    with pytest.raises(OSError) as e:
        SerialPort.open(pty_pair[2])
    assert e.value.errno == errno.ENOSYS


def test_reset_input_buffer_drops_pending_bytes(pty_pair, port):
    os.write(pty_pair[0], b"boot rom text")
    time.sleep(0.05)
    port.reset_input_buffer()
    assert port.read(64, timeout=0.1) == b""
    os.write(pty_pair[0], b"after")
    assert port.read(64, timeout=1) == b"after"


def test_reset_input_buffer_closed(pty_pair):
    p = SerialPort.open(pty_pair[2])
    p.close()
    with pytest.raises(ValueError):
        p.reset_input_buffer()


def test_same_tty_state_as_pyserial(pty_pair):
    """Oracle: pyserial 3.5 (not vendored; runs when installed, e.g. `--with pyserial==3.5`)."""
    serial = pytest.importorskip("serial")
    m2, s2 = os.openpty()
    try:
        serial.Serial(os.ttyname(s2), 115200).close()
        theirs = termios.tcgetattr(s2)
        SerialPort.open(pty_pair[2]).close()
        ours = termios.tcgetattr(pty_pair[1])
    finally:
        os.close(m2), os.close(s2)
    assert ours[0] == theirs[0] and ours[1] == theirs[1] and ours[3] == theirs[3]  # iflag, oflag, lflag
    keep = termios.CSIZE | termios.PARENB | termios.CSTOPB | termios.CREAD | termios.CLOCAL
    assert ours[2] & keep == theirs[2] & keep
    assert ours[6][termios.VMIN:termios.VTIME + 1] == theirs[6][termios.VMIN:termios.VTIME + 1]
