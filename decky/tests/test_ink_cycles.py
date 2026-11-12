"""decky/tools/ink_cycles.py against the emulator: values change, partial refreshes follow, a screen switch is a full refresh."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import ink_cycles  # noqa: E402


def test_cycles_run_on_the_emulator(tmp_path, capsys):
    rc = ink_cycles.main(["--emulator", str(tmp_path), "--seconds", "7", "--tick-s", "0.5", "--screens", "telemetry,thermals-power",
                          "--switch-s", "5"])
    out = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert rc == 0 and out["naks"] == 0 and out["last_error"] is None
    assert out["partial_refreshes"] >= 3 and out["full_refreshes"] >= 2   # first frame + the switch to the second screen


def test_unknown_screen_is_refused(tmp_path):
    assert ink_cycles.main(["--emulator", str(tmp_path), "--seconds", "1", "--screens", "nope"]) == 2
