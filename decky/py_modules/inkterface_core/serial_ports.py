import glob
import re


def _natural(path: str):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", path)]


def list_serial_ports() -> list[str]:
    """USB CDC/serial device nodes, naturally sorted (ttyACM2 < ttyACM10)."""
    return sorted(glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*"), key=_natural)
