"""Static checks on the plugin shell. Behaviour is tested in tests/plugin against a fake `decky` module."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_main_fails_cleanly_without_decky(monkeypatch):
    monkeypatch.setitem(sys.modules, "decky", None)  # import raises ImportError
    spec = importlib.util.spec_from_file_location("plugin_main2", ROOT / "main.py")
    with pytest.raises(ImportError):
        spec.loader.exec_module(importlib.util.module_from_spec(spec))


def test_core_has_no_decky_imports():
    import ast

    for f in (ROOT / "py_modules" / "inkterface_core").rglob("*.py"):
        for n in ast.walk(ast.parse(f.read_text())):
            names = [a.name for a in n.names] if isinstance(n, ast.Import) else [n.module or ""] if isinstance(n, ast.ImportFrom) else []
            assert not any(x.split(".")[0] == "decky" for x in names), f


def test_plugin_json():
    j = json.loads((ROOT / "plugin.json").read_text())
    assert j["name"] == "Inkterface" and j["api_version"] == 1
    assert "root" not in j["flags"]  # runs as `deck`; the real Steam Machine grants it the panel via a uaccess ACL (readme, Serial permission)
