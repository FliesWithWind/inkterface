"""Regression: edge_test.py crashed on the real panel at `P.mode_ex(q, MODE_HOST, 14)` because proto.mode_payload (rightly)
refuses an out-of-range timeout before it reaches the wire. Hostile payloads in the tool must be built raw."""
import ast
import struct
from pathlib import Path

from inkterface_core import proto as P

SRC = (Path(__file__).resolve().parents[1] / "tools" / "edge_test.py").read_text()


def test_every_constant_mode_call_in_edge_test_is_encodable():
    calls = 0
    for n in ast.walk(ast.parse(SRC)):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ("mode_ex", "mode_payload"):
            rest = n.args[1:]
            if n.func.attr == "mode_ex" and all(isinstance(a, (ast.Constant, ast.Attribute)) for a in rest):
                P.mode_ex(0, *[eval(compile(ast.Expression(a), "x", "eval"), {"P": P}) for a in rest])   # ProtocolError = tool cannot build it
                calls += 1
    assert calls >= 2


def test_out_of_range_timeout_is_sent_raw():
    assert 'struct.pack("<BH", P.MODE_HOST, 14)' in SRC
    assert struct.pack("<BH", P.MODE_HOST, 14) == b"\x00\x0e\x00"
