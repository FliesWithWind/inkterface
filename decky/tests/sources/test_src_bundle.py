"""The sources package must load in Decky's bundled Python (no extra stdlib modules, no site-packages),
and the dump tool must work and redact secrets."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
import sourcefs as F

DECKY = Path(__file__).resolve().parents[2]
BUNDLED = DECKY / "tests" / "decky_3.3.1_stdlib.txt"

SNIPPET = """
import sys
sys.path[:] = [{pm!r}] + [p for p in sys.path if 'site-packages' not in p and 'dist-packages' not in p]
before = set(sys.modules)
from inkterface_core import sources as S
r = S.default_registry({{}}, root={root!r}, home={root!r}, network=False)
r.poll_due(force=True)
assert 'system.cpu.load' in r.snapshot()
new = {{m for m in set(sys.modules) - before if m.split('.')[0] in sys.stdlib_module_names}}
missing = sorted(m for m in new - set(open({bundled!r}).read().split()))
assert not missing, 'stdlib modules not in Decky bundle: %s' % missing
assert 'zoneinfo' not in sys.modules, 'zoneinfo must stay a lazy import'
"""


def run(tmp_path, bundled):
    code = SNIPPET.format(pm=str(DECKY / "py_modules"), root=str(tmp_path), bundled=str(bundled))
    return subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True)


def test_sources_import_with_only_bundled_stdlib(tmp_path):
    r = run(tmp_path, BUNDLED)
    assert r.returncode == 0, r.stderr


def test_bundle_guard_catches_a_missing_module(tmp_path):
    short = tmp_path / "short.txt"
    short.write_text("\n".join(m for m in BUNDLED.read_text().split() if m != "urllib.request"))
    r = run(tmp_path, short)
    assert r.returncode != 0 and "urllib.request" in r.stderr


def load_tool():
    spec = importlib.util.spec_from_file_location("dump_sources", DECKY / "tools" / "dump_sources.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_dump_sources_offline_prints_json(tmp_path, capsys):
    steam = tmp_path / "Steam"
    F.w(steam, "steamapps/appmanifest_730.acf", '"AppState" { "name" "Counter-Strike 2" }')
    m = load_tool()
    assert m.main(["--steam-dir", str(steam), "--now-playing", "730", "--tz", "UTC"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["steam.now_playing"]["name"] == "Counter-Strike 2"
    assert set(out["_meta"]["sources"]) == {"time", "system", "steam_local"}  # --live not given: no network sources
    assert "system.cpu.load.history" in out


def test_dump_sources_redacts_key_and_steamid(monkeypatch):
    m = load_tool()
    text = 'x KEYKEYKEY y 76561198012345678 z 12345678901234567'
    out = m.redact(text, ("KEYKEYKEY", None))
    assert "KEYKEYKEY" not in out and "76561198012345678" not in out and "<steamid64>" in out
    assert "12345678901234567" in out  # only steamid64-shaped numbers


def test_base_source_poll_is_abstract():
    from inkterface_core.sources import Source
    with pytest.raises(NotImplementedError):
        Source().poll()
