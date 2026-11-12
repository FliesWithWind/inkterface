import glob

from inkterface_core import list_serial_ports


def fake(monkeypatch, table):
    monkeypatch.setattr(glob, "glob", lambda pat: list(table.get(pat, [])))


def test_natural_sort_across_kinds(monkeypatch):
    fake(monkeypatch, {"/dev/ttyACM*": ["/dev/ttyACM10", "/dev/ttyACM2", "/dev/ttyACM0"], "/dev/ttyUSB*": ["/dev/ttyUSB1"]})
    assert list_serial_ports() == ["/dev/ttyACM0", "/dev/ttyACM2", "/dev/ttyACM10", "/dev/ttyUSB1"]


def test_empty(monkeypatch):
    fake(monkeypatch, {})
    assert list_serial_ports() == []


def test_real_glob_returns_list_of_str():
    out = list_serial_ports()
    assert isinstance(out, list) and all(isinstance(p, str) and p.startswith("/dev/tty") for p in out)
