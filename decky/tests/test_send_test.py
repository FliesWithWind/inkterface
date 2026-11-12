"""Run tools/send_test.py end to end against the emulator through a fake serial port."""
import importlib.util
import os

import pytest

from inkterface_core import usb_link  # noqa: E402
from inkterface_core.emulator import EmulatedPanel  # noqa: E402


class FakeSerial:
    def __init__(self, em):
        self.em, self.rx = em, bytearray()

    def write(self, b, timeout=None):
        self.rx += self.em.feed(bytes(b))

    def read(self, n, timeout=None):
        out, self.rx = bytes(self.rx[:n]), self.rx[n:]
        return out

    def reset_input_buffer(self):
        self.rx.clear()

    def close(self):
        pass


def test_send_test_against_emulator(monkeypatch, capsys):
    path = os.path.join(os.path.dirname(__file__), "..", "tools", "send_test.py")
    spec = importlib.util.spec_from_file_location("send_test", path)
    st = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(st)
    em = EmulatedPanel()
    monkeypatch.setattr(usb_link, "find_panels", lambda s=None: ["/dev/fake"])
    monkeypatch.setattr(usb_link, "is_espressif", lambda p: True)
    monkeypatch.setattr(usb_link, "open_port", lambda p: FakeSerial(em))
    monkeypatch.setattr("sys.argv", ["send_test.py"])
    assert st.main() == 0, capsys.readouterr().out
    assert "ALL OK" in capsys.readouterr().out
    assert [r[0] for r in em.refreshes] == ["F", "P", "P"]
    assert em.mode == 1  # handed back


def load_st():
    path = os.path.join(os.path.dirname(__file__), "..", "tools", "send_test.py")
    spec = importlib.util.spec_from_file_location("send_test", path)
    st = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(st)
    return st


class DeadSerial(FakeSerial):
    def write(self, b, timeout=None):
        pass


def patch_port(monkeypatch, serial_obj, argv):
    monkeypatch.setattr(usb_link, "find_panels", lambda s=None: ["/dev/fake"])
    monkeypatch.setattr(usb_link, "is_espressif", lambda p: True)
    monkeypatch.setattr(usb_link, "open_port", lambda p: serial_obj)
    monkeypatch.setattr("sys.argv", ["send_test.py"] + argv)


def test_no_hello_returns_failure(monkeypatch, capsys):
    patch_port(monkeypatch, DeadSerial(EmulatedPanel()), ["--hello-timeout", "0.6"])
    assert load_st().main() == 1
    assert "FAIL" in capsys.readouterr().out


def test_refuses_non_espressif_port(monkeypatch):
    opened = []
    monkeypatch.setattr(usb_link, "find_panels", lambda s=None: [])
    monkeypatch.setattr(usb_link, "is_espressif", lambda p: False)
    monkeypatch.setattr(usb_link, "open_port", lambda p: opened.append(p))
    monkeypatch.setattr("sys.argv", ["send_test.py", "/dev/ttyACM0"])
    with pytest.raises(SystemExit) as e:
        load_st().main()
    assert "not an Espressif" in str(e.value) and opened == []  # never opened


def test_ambiguous_or_missing_autodetect_exits(monkeypatch):
    st = load_st()
    monkeypatch.setattr("sys.argv", ["send_test.py"])
    for found in ([], ["/dev/a", "/dev/b"]):
        monkeypatch.setattr(usb_link, "find_panels", lambda s=None, f=found: f)
        with pytest.raises(SystemExit):
            st.main()


def test_list_mode(monkeypatch, capsys):
    monkeypatch.setattr(usb_link, "find_panels", lambda s=None: ["/dev/ttyACM2"])
    monkeypatch.setattr("sys.argv", ["send_test.py", "--list"])
    assert load_st().main() == 0
    assert "/dev/ttyACM2" in capsys.readouterr().out


def test_rect_nak_marks_run_failed(monkeypatch, capsys):
    """A panel that NAKs every RECT must make send_test fail, not pass."""

    class NakPanel(EmulatedPanel):
        def _rect(self, f):
            return self._nak(f.seq, 3)

    patch_port(monkeypatch, FakeSerial(NakPanel()), [])
    assert load_st().main() == 1
    assert "BAD_RECT" in capsys.readouterr().out


def test_pattern_is_deterministic_and_not_blank():
    st = load_st()
    a, b = st.pattern(), st.pattern()
    assert a == b and len(a) == 38880 and 0 < sum(bin(x).count("1") for x in a) < 38880 * 8
