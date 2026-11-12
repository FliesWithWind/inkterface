"""tools/run_engine.py. Never touches a real panel: emulator mode, or a --serial that matches nothing."""
import importlib.util
import json
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[2] / "tools" / "run_engine.py"
spec = importlib.util.spec_from_file_location("run_engine_tool", TOOL)
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


def test_emulator_run_writes_pngs_and_prints_a_summary(tmp_path, capsys):
    out = tmp_path / "pngs"
    assert tool.main(["--emulator", str(out), "--seconds", "2.5", "--no-sim-refresh"]) == 0
    s = json.loads(capsys.readouterr().out)
    assert s["state"] == "stopped" and s["full_refreshes"] >= 1 and s["frames_rendered"] >= 2
    assert s["rects_sent"] >= 10 and s["last_error"] is None and s["screens_shown"] == ["telemetry"]
    pngs = sorted(out.glob("refresh_*.png"))
    assert len(pngs) == len(s["panel_refreshes"]) >= 1
    assert s["panel_refreshes"][0]["kind"] == "F" and s["panel_refreshes"][0]["png"] == pngs[0].name


def test_settings_file_pin_and_error_reporting(tmp_path, capsys):
    cfg = tmp_path / "s.json"
    cfg.write_text(json.dumps({"pinned": "big-clock"}))
    assert tool.main(["--emulator", str(tmp_path / "o"), "--seconds", "1.5", "--no-sim-refresh", "--settings", str(cfg)]) == 0
    assert json.loads(capsys.readouterr().out)["screens_shown"] == ["big-clock"]

    cfg.write_text("{broken")
    assert tool.main(["--emulator", str(tmp_path / "o2"), "--seconds", "1.5", "--no-sim-refresh", "--settings", str(cfg)]) == 0
    cap = capsys.readouterr()
    assert "using defaults" in cap.err and json.loads(cap.out)["screens_shown"] == ["telemetry"]


def test_extra_screens_dir_and_bad_package_reported(tmp_path, capsys):
    extra = tmp_path / "extra"
    (extra / "broken").mkdir(parents=True)
    (extra / "broken" / "screen.json").write_text("{nope")
    assert tool.main(["--emulator", str(tmp_path / "o"), "--seconds", "1", "--no-sim-refresh", "--screens", str(extra)]) == 0
    assert "screen: broken:" in capsys.readouterr().err


def test_no_panel_exits_nonzero_without_opening_anything(capsys):
    # A serial number nothing has: usb_connect finds no candidate, so no port is ever opened.
    assert tool.main(["--serial", "no-such-serial-0000", "--seconds", "1"]) == 1
    s = json.loads(capsys.readouterr().out)
    assert s["state"] == "stopped" and "no panel found" in s["last_error"] and "panel_refreshes" not in s
