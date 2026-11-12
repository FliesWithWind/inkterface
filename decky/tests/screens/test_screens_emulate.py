import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import BUILTIN_DIR, DECKY
from PIL import Image
from screens_testlib import valid_doc

from inkterface_core.screens import emulate
from inkterface_core.screens.emulate import main


def test_single_package_with_sample_data(tmp_path, capsys):
    out = tmp_path / "t.png"
    assert main([str(BUILTIN_DIR / "telemetry"), "--out", str(out)]) == 0
    img = Image.open(out)
    assert img.size == (648, 480) and img.mode == "1"
    assert "ok" in capsys.readouterr().out
    # same pixels as the golden: default data file is <pkg>/sample-data.json
    assert img.tobytes() == Image.open(Path(__file__).parent / "goldens" / "telemetry.png").convert("1").tobytes()


def test_explicit_data_file_changes_output(tmp_path):
    data = tmp_path / "d.json"
    data.write_text(json.dumps({"system.cpu.load": 99, "system.cpu.load.history": [1, 99, 50]}))
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    assert main([str(BUILTIN_DIR / "telemetry"), "--data", str(data), "--out", str(a)]) == 0
    assert main([str(BUILTIN_DIR / "telemetry"), "--out", str(b)]) == 0
    assert a.read_bytes() != b.read_bytes()


def test_no_data_file_renders_placeholders(make_pkg, tmp_path):
    pkg = make_pkg(valid_doc())
    assert main([str(pkg), "--out", str(tmp_path / "x" / "deep" / "o.png")]) == 0     # creates parent dirs
    assert (tmp_path / "x" / "deep" / "o.png").is_file()


def test_default_output_name_is_package_id(make_pkg, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main([str(make_pkg(valid_doc()))]) == 0
    assert (tmp_path / "demo.png").is_file()


def test_set_overrides_settings(tmp_path):
    a, b, c = (tmp_path / n for n in "abc")
    pkg = str(BUILTIN_DIR / "big-clock")
    assert main([pkg, "--out", str(a)]) == 0
    assert main([pkg, "--out", str(b), "--set", "time_format=%I:%M"]) == 0
    assert main([pkg, "--out", str(c), "--set", 'time_format="%I:%M"']) == 0     # JSON string works too
    assert a.read_bytes() != b.read_bytes() and b.read_bytes() == c.read_bytes()


def test_set_errors(tmp_path, capsys):
    pkg = str(BUILTIN_DIR / "big-clock")
    with pytest.raises(SystemExit) as e:
        main([pkg, "--set", "novalue"])
    assert e.value.code == 2 and "expected key=value" in capsys.readouterr().err
    assert main([pkg, "--out", str(tmp_path / "o.png"), "--set", "nope=1"]) == 1
    assert "settings.nope: unknown setting" in capsys.readouterr().err


def test_invalid_package_exits_1_with_all_messages(make_pkg, tmp_path, capsys):
    bad = valid_doc()
    bad["active"]["widgets"] = [{"type": "number", "x": 0, "y": 0, "w": 10, "h": 10, "bind": "cpu.tmp"},
                                {"type": "bar", "x": 700, "y": 0, "w": 10, "h": 10, "bind": "system.cpu.load"}]
    assert main([str(make_pkg(bad)), "--out", str(tmp_path / "o.png")]) == 1
    err = capsys.readouterr().err
    assert "active.widgets[0].bind: unknown source 'cpu.tmp'" in err and "active.widgets[1].x:" in err
    assert not (tmp_path / "o.png").exists()


def test_missing_package(tmp_path, capsys):
    assert main([str(tmp_path / "nope")]) == 1
    assert "not found" in capsys.readouterr().err


@pytest.mark.parametrize("content,msg", [("{bad", "cannot read data"), ("[1]", "top level must be an object")])
def test_bad_data_file(make_pkg, tmp_path, capsys, content, msg):
    f = tmp_path / "d.json"
    f.write_text(content)
    assert main([str(make_pkg(valid_doc())), "--data", str(f), "--out", str(tmp_path / "o.png")]) == 1
    assert msg in capsys.readouterr().err


def test_all_renders_every_package(tmp_path, capsys):
    out = tmp_path / "out"
    assert main(["--all", str(BUILTIN_DIR), "--out", str(out)]) == 0
    assert sorted(p.name for p in out.iterdir()) == ["big-clock.png", "now-playing.png", "telemetry.png", "thermals-power.png"]
    out_txt = capsys.readouterr().out
    assert out_txt.count("ok ") == 4
    assert out_txt.count("(idle-only)") == 4  # clock-weather(+gray4), steam-stats, minimal-clock: no active section


def test_all_continues_past_failures_and_skips_non_packages(make_pkg, tmp_path, capsys):
    root = tmp_path / "pkgs"
    root.mkdir()
    good = make_pkg(valid_doc(id="good"))
    bad = make_pkg(valid_doc(id="bad", version="x"))
    for p in (good, bad):
        p.rename(root / p.name)
    (root / "notapackage").mkdir()
    (root / "stray.txt").write_text("x")
    assert main(["--all", str(root), "--out", str(tmp_path / "o")]) == 1
    cap = capsys.readouterr()
    assert (tmp_path / "o" / "good.png").is_file() and not (tmp_path / "o" / "bad.png").exists()
    assert "FAIL  bad" in cap.err and "version" in cap.err and "notapackage" not in cap.out + cap.err


def test_all_default_out_dir(make_pkg, tmp_path, monkeypatch):
    root = tmp_path / "pkgs"
    root.mkdir()
    make_pkg(valid_doc(id="good")).rename(root / "good")
    monkeypatch.chdir(tmp_path)
    assert main(["--all", str(root)]) == 0
    assert (tmp_path / "out" / "good.png").is_file()


def test_all_with_empty_or_missing_dir(tmp_path, capsys):
    (tmp_path / "empty").mkdir()
    assert main(["--all", str(tmp_path / "empty")]) == 1
    assert main(["--all", str(tmp_path / "missing")]) == 1
    assert "no screen packages" in capsys.readouterr().err


def test_all_with_shared_data_file(make_pkg, tmp_path):
    root = tmp_path / "pkgs"
    root.mkdir()
    make_pkg(valid_doc(id="one")).rename(root / "one")
    f = tmp_path / "d.json"
    f.write_text("{}")
    assert main(["--all", str(root), "--data", str(f), "--out", str(tmp_path / "o")]) == 0


def test_usage_errors(capsys):
    for argv in ([], ["a", "--all", "b"]):
        with pytest.raises(SystemExit) as e:
            main(argv)
        assert e.value.code == 2
    assert "either a package folder or --all" in capsys.readouterr().err


def test_python_dash_m_entry_point(tmp_path):
    env = {**os.environ, "PYTHONPATH": str(DECKY / "py_modules")}
    out = tmp_path / "cli.png"
    r = subprocess.run([sys.executable, "-m", "inkterface_core.screens.emulate", str(BUILTIN_DIR / "big-clock"),
                        "--out", str(out)], capture_output=True, text=True, env=env, cwd=tmp_path, timeout=60)
    assert r.returncode == 0, r.stderr
    assert Image.open(out).size == (648, 480)
    bad = subprocess.run([sys.executable, "-m", "inkterface_core.screens.emulate", str(tmp_path)], capture_output=True,
                         text=True, env=env, cwd=tmp_path, timeout=60)
    assert bad.returncode == 1 and "screen.json" in bad.stderr


def test_emulate_returns_screen(tmp_path):
    s = emulate.emulate(BUILTIN_DIR / "now-playing", tmp_path / "n.png")
    assert s.id == "now-playing"
