"""pyserial needs termios, which Decky's bundle lacks: nothing shipped or run by the plugin may use it."""
import pathlib
import re

DECKY = pathlib.Path(__file__).resolve().parents[1]
PATTERN = re.compile(r"^\s*(import serial|from serial\b)", re.M)


def test_pyserial_not_vendored():
    names = {p.name.lower() for p in (DECKY / "py_modules").iterdir()}
    assert "serial" not in names and not any(n.startswith("pyserial") for n in names)


def test_no_pyserial_import_in_core_or_tools():
    files = [*(DECKY / "py_modules" / "inkterface_core").glob("*.py"), *(DECKY / "tools").glob("*.py"), DECKY / "main.py"]
    assert len(files) > 8
    assert [str(f) for f in files if PATTERN.search(f.read_text())] == []
