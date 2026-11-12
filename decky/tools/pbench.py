#!/usr/bin/env python3
"""Drive the ee05_pbench firmware (firmware/src/pbench/main.cpp) and summarise timings.

  pbench.py CMD [CMD ...]      each CMD is one bench line, e.g. "set drive 12" "run 0 0 64 32 20 0 0"
  pbench.py --port PATH ...    default: the first Espressif USB-Serial-JTAG by-id port

Prints every reply line. "T ..." lines are per-refresh timings (microseconds):
  T k total init pon data refresh poff busylat
"""
import glob
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "py_modules"))
from inkterface_core.usb_link import open_port  # noqa: E402


def default_port() -> str:
    ports = sorted(glob.glob("/dev/serial/by-id/usb-Espressif_USB_JTAG*-if00"))
    if not ports:
        sys.exit("no Espressif USB-Serial-JTAG port (never use ttyACM*: game controllers)")
    return ports[0]


def run(ser, line: str, timeout: float = 600.0):
    ser.write((line + "\n").encode())
    out, buf, end = [], b"", time.time() + timeout
    while time.time() < end:
        buf += ser.read(4096)
        while b"\n" in buf:
            l, buf = buf.split(b"\n", 1)
            l = l.decode(errors="replace").strip()
            if l:
                out.append(l)
                print(l, flush=True)
            if l.startswith(("OK", "ERR")):
                return out
    raise TimeoutError(line)


def main(argv):
    port = default_port()
    if argv[:1] == ["--port"]:
        port, argv = argv[1], argv[2:]
    ser = open_port(port)
    ser.reset_input_buffer()
    for line in argv:
        print(f"> {line}", flush=True)
        run(ser, line)
    ser.close()


if __name__ == "__main__":
    main(sys.argv[1:])
