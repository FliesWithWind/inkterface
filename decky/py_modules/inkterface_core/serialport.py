"""Raw serial port on Linux using only modules present in Decky Loader's PyInstaller bundle.

Why not pyserial: it imports `termios`, which Decky's bundle omits (see docs/decky-runtime.md). `fcntl.ioctl` is
bundled and talks to the tty driver directly with the kernel `struct termios`.

Scope: Linux USB-CDC/ACM (ESP32-S3 USB-Serial-JTAG). Baud/parity/stop bits are left untouched
(ignored by CDC). Linux generic ioctl numbers: valid on x86_64 and arm64.
"""
import errno
import fcntl
import os
import select
import struct
import sys

# <asm-generic/ioctls.h>
TCGETS, TCSETS = 0x5401, 0x5402
TIOCMBIS, TIOCMBIC = 0x5416, 0x5417
TCFLSH, TCIFLUSH = 0x540B, 0
TIOCM_DTR, TIOCM_RTS = 0x002, 0x004
# <asm-generic/termbits.h>: struct termios = 4 x uint32 flags, uint8 c_line, uint8 c_cc[19]
_FMT = "=IIII B 19s"
_SIZE = struct.calcsize(_FMT)  # 36
IGNBRK, BRKINT, PARMRK, ISTRIP, INLCR, IGNCR, ICRNL = 0o1, 0o2, 0o10, 0o40, 0o100, 0o200, 0o400
IXON, IXANY, IXOFF = 0o2000, 0o4000, 0o10000
CSIZE, CS8, PARENB, CREAD, CLOCAL = 0o60, 0o60, 0o400, 0o200, 0o4000
VTIME, VMIN = 5, 6
# Errors from the modem-control ioctls that only mean "this tty has no modem lines" (pty, some adapters).
_NO_MODEM = (errno.ENOTTY, errno.EINVAL, errno.EOPNOTSUPP)


def _make_raw(fd: int) -> None:
    iflag, oflag, cflag, lflag, line, cc = struct.unpack(_FMT, fcntl.ioctl(fd, TCGETS, bytes(_SIZE)))
    iflag &= ~(IGNBRK | BRKINT | PARMRK | ISTRIP | INLCR | IGNCR | ICRNL | IXON | IXOFF | IXANY)
    oflag = 0  # no output post-processing (OPOST/ONLCR/...)
    cflag = (cflag & ~(CSIZE | PARENB)) | CS8 | CREAD | CLOCAL
    lflag = 0  # no echo, no canonical mode, no signals (ECHO/ICANON/ISIG/IEXTEN/...)
    cc = bytearray(cc)
    cc[VMIN] = cc[VTIME] = 0  # non-blocking reads; timeouts are done with select()
    fcntl.ioctl(fd, TCSETS, struct.pack(_FMT, iflag, oflag, cflag, lflag, line, bytes(cc)))


def _modem(fd: int, op: int, bits: int) -> None:
    if not bits:
        return
    try:
        fcntl.ioctl(fd, op, struct.pack("I", bits))
    except OSError as e:
        if e.errno not in _NO_MODEM:
            raise


class SerialPort:
    """open() / read() / write() / close(). Bytes pass through unmodified (8N1 raw, no flow control).

    dtr/rts default to False (low) because on ESP32-S3 USB-Serial-JTAG DTR/RTS edges reset the chip
    or enter the bootloader. ponytail: the kernel raises DTR/RTS inside open(), so a brief high pulse
    before we lower them cannot be avoided from userspace (pyserial has the same limit).
    """

    def __init__(self, fd: int):
        self._fd = fd

    @classmethod
    def open(cls, path: str, *, dtr: bool = False, rts: bool = False) -> "SerialPort":
        if not sys.platform.startswith("linux"):
            raise OSError(errno.ENOSYS, "SerialPort supports Linux only")
        fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            _make_raw(fd)  # raises ENOTTY for non-tty files
            _modem(fd, TIOCMBIS, (TIOCM_DTR if dtr else 0) | (TIOCM_RTS if rts else 0))
            _modem(fd, TIOCMBIC, (0 if dtr else TIOCM_DTR) | (0 if rts else TIOCM_RTS))
        except BaseException:
            os.close(fd)
            raise
        return cls(fd)

    def _check(self) -> int:
        if self._fd is None:
            raise ValueError("port is closed")
        return self._fd

    def read(self, size: int = 4096, timeout: float | None = None) -> bytes:
        """Up to `size` bytes (>=1) as soon as any arrive; b"" only on timeout. OSError(EIO) if the device vanished."""
        fd = self._check()
        if size <= 0:
            return b""
        if not select.select([fd], [], [], timeout)[0]:
            return b""
        data = os.read(fd, size)  # VMIN=0: never EAGAIN; single reader assumed (shortcut: no read lock)
        if not data:  # readable but EOF: tty hung up (USB unplugged); a timeout is the only other way to get b""
            raise OSError(errno.EIO, "serial device disconnected")
        return data

    def write(self, data: bytes, timeout: float | None = None) -> None:
        """Write all of `data`; TimeoutError if the device stops accepting bytes for `timeout` seconds."""
        fd = self._check()
        view = memoryview(data)
        while view:
            try:
                view = view[os.write(fd, view):]
            except BlockingIOError:
                if not select.select([], [fd], [], timeout)[1]:
                    raise TimeoutError("serial write timed out") from None

    def reset_input_buffer(self) -> None:
        """Discard bytes the kernel has received but we have not read (boot/ROM text)."""
        fcntl.ioctl(self._check(), TCFLSH, TCIFLUSH)

    def close(self) -> None:
        if self._fd is not None:
            fd, self._fd = self._fd, None
            os.close(fd)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
