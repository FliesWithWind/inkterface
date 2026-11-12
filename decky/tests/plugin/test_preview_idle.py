"""preview_idle_screen: the panel's own idle renderer on the host. Uses the real harness built with g++ (like
tests/idle/test_goldens.py) and fake executables for the failure paths. Time is frozen only in the golden comparisons."""
import base64
import hashlib
import io
import json
import os
import stat
import sys
from pathlib import Path

import pytest
from packages_testlib import GOOD_CODE, package_zip, screen_json
from PIL import Image

from inkterface_core import plugin_core
from inkterface_core.idle import preview

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "idle"))
import idlerender as R  # noqa: E402

DECKY = Path(__file__).resolve().parents[2]
GOLD = DECKY / "tests" / "idle" / "goldens"
SCREENS = DECKY / "screens"
pytestmark = pytest.mark.skipif(not R.have_gpp(), reason="g++ not available")
WIFI, KEY = "hunter2-wifi-pass", "0123456789abcdef0123456789abcdef"


@pytest.fixture(scope="module")
def exe(tmp_path_factory):
    return R.build_harness(str(tmp_path_factory.mktemp("idle-harness")))


@pytest.fixture
def real(exe, monkeypatch):
    monkeypatch.setattr(plugin_core, "IDLE_RENDER_BIN", Path(exe))
    return Path(exe)


def decode(r):
    assert r["ok"] is True, r
    head = "data:image/png;base64,"
    assert r["data_url"].startswith(head)
    raw = base64.b64decode(r["data_url"][len(head):], validate=True)
    return Image.open(io.BytesIO(raw))


def script(tmp_path, body, name="fake_render"):
    p = tmp_path / name
    p.write_text("#!/bin/sh\n" + body + "\n")
    p.chmod(0o755)
    return p


TEXT = {"type": "text", "x": 0, "y": 0, "w": 300, "h": 40, "font": "bold-18", "text": "HELLO"}


def idle_only_zip(pid="idle-demo", **extra):
    """A minimal idle-only package that really compiles (test_callables.idle_only_zip has no text, so it does not)."""
    doc = {"schema": 1, "id": pid, "name": "Idle demo", "version": "1.0.0", "author": "T",
           "idle": {"render_mode": "mono", "widgets": [TEXT]}}
    return package_zip(pid, doc=doc, extra=extra)


def idle_code_zip(pid="idle-code"):
    doc = screen_json(pid, code=True, idle={"render_mode": "mono", "widgets": [TEXT]})
    return package_zip(pid, doc=doc, code=GOOD_CODE)


def gray_only_zip(pid="gray-only"):
    """A text widget in gray compiles in gray4 but is rejected in mono."""
    doc = {"schema": 1, "id": pid, "name": "Gray only", "version": "1.0.0", "author": "T",
           "idle": {"render_mode": "gray4", "widgets": [{**TEXT, "color": 1}]}}
    return package_zip(pid, doc=doc)


# -- real renderer ------------------------------------------------------------------------------
def test_builtin_clock_weather_mono_is_a_648x480_one_bit_png(h, real):
    async def body(p):
        im = decode(await p.preview_idle_screen("clock-weather", "mono"))
        assert im.size == (648, 480) and im.mode == "1"
        assert len(im.getcolors()) == 2
    h.run(body, wait_connected=False)


def test_gray4_uses_real_grays_and_mono_exactly_two_levels(h, real):
    async def body(p):
        mono = decode(await p.preview_idle_screen("clock-weather", "mono"))
        gray = decode(await p.preview_idle_screen("clock-weather-gray4", "gray4"))
        assert mono.mode == "1" and len(mono.getcolors()) == 2
        assert gray.size == (648, 480) and gray.mode == "L"
        assert len(gray.getcolors()) > 2 and {v for _, v in gray.getcolors()} <= {0, 85, 170, 255}
        assert gray.tobytes() != mono.convert("L").tobytes()
        # a layout that was written for 4 grays cannot be previewed as mono
        r = await p.preview_idle_screen("clock-weather-gray4", "mono")
        assert r["ok"] is False and "cannot be shown in mono mode" in r["error"]
    h.run(body, wait_connected=False)


def test_mode_none_follows_the_users_choice_then_the_declared_mode(h, real):
    async def body(p):
        assert decode(await p.preview_idle_screen("clock-weather")).mode == "1"            # declared mono
        assert decode(await p.preview_idle_screen("clock-weather", None)).mode == "1"
        assert decode(await p.preview_idle_screen("clock-weather-gray4")).mode == "L"      # declared gray4
        r = await p.set_settings({"idle_screens": ["clock-weather"], "idle_modes": {"clock-weather": "gray4"}})
        assert r["ok"]
        im = decode(await p.preview_idle_screen("clock-weather"))
        assert im.mode == "L"                                                               # the user's 4-gray choice
        assert decode(await p.preview_idle_screen("clock-weather", "mono")).mode == "1"    # an explicit mode wins
    h.run(body, wait_connected=False)


@pytest.mark.parametrize("sid", ["clock-weather", "clock-weather-gray4", "steam-stats", "minimal-clock"])
def test_matches_the_golden_once_time_and_zone_are_frozen(h, real, monkeypatch, sid):
    """The clock is live in production. Here `time.time` (in preview.py) and posix_tz are pinned to the sample's own
    epoch / zone, which makes the result pixel-identical to the firmware-renderer goldens in tests/idle/goldens."""
    sample = json.loads((SCREENS / sid / "sample-data.json").read_text())

    class Frozen:
        @staticmethod
        def time():
            return sample["epoch"]

    monkeypatch.setattr(preview, "time", Frozen)
    monkeypatch.setattr(plugin_core, "posix_tz", lambda: sample["tz"])

    async def body(p):
        im = decode(await p.preview_idle_screen(sid))
        gold = Image.open(GOLD / f"{sid}.png")
        assert im.size == gold.size == (648, 480) and im.mode == gold.mode
        assert im.tobytes() == gold.tobytes(), f"{sid} differs from its golden"
    h.run(body, wait_connected=False)


def test_the_clock_is_live_not_the_samples_frozen_time(h, real, monkeypatch):
    seen = []
    real_run = preview.subprocess.run

    def spy(argv, **kw):
        seen.append(Path(argv[2]).read_text())
        return real_run(argv, **kw)

    monkeypatch.setattr(preview.subprocess, "run", spy)
    monkeypatch.setattr(preview, "time", type("T", (), {"time": staticmethod(lambda: 1_900_000_123.9)}))
    monkeypatch.setattr(plugin_core, "posix_tz", lambda: "UTC0")

    async def body(p):
        decode(await p.preview_idle_screen("clock-weather"))
        assert "epoch 1900000123\n" in seen[0] and "tz UTC0\n" in seen[0]
        assert "epoch 1794316020" not in seen[0] and "CET-1CEST" not in seen[0]
        assert "wx.current" not in seen[0] and "1 current.temp 0 N 11.4" in seen[0]   # sample values still used
    h.run(body, wait_connected=False)


def test_idle_only_package_works(h, real):
    async def body(p):
        assert (await p.import_package(h.package("i.zip", idle_only_zip())))["ok"]
        im = decode(await p.preview_idle_screen("idle-demo"))
        assert im.size == (648, 480) and im.mode == "1"
    h.run(body, wait_connected=False)


def test_package_without_sample_data_draws_placeholders(h, real):
    async def body(p):
        await p.import_package(h.package("i.zip", idle_only_zip()))
        assert not (p.core.store.get("idle-demo").path / "sample-data.json").exists()
        assert decode(await p.preview_idle_screen("idle-demo")).size == (648, 480)
    h.run(body, wait_connected=False)


def test_bad_sample_data_is_a_clean_error(h, real):
    async def body(p):
        await p.import_package(h.package("i.zip", idle_only_zip()))
        sd = p.core.store.get("idle-demo").path / "sample-data.json"
        sd.write_text(json.dumps({"values": {"nosuch.x": 1}}))
        r = await p.preview_idle_screen("idle-demo")
        assert r["ok"] is False and "sample-data.json" in r["error"]
        sd.write_text("{ not json")   # unreadable sample data is the same as none
        assert (await p.preview_idle_screen("idle-demo"))["ok"]
        sd.write_text(json.dumps({"values": {"a\nb": 1}}))
        assert (await p.preview_idle_screen("idle-demo"))["ok"] is False
    h.run(body, wait_connected=False)


def test_newlines_in_sample_text_cannot_inject_records():
    from inkterface_core.idle.preview import sample_lines
    out = sample_lines({"sources": {"s": {}}}, {"values": {"s.name": "x\n0 other.path 0 N 1\r\nepoch 5"}})
    assert out.count("\n") == 1 and out.startswith("0 name 0 S x 0 other.path")


# -- input validation ------------------------------------------------------------------------------
@pytest.mark.parametrize("sid", ["nope", None, 5, "", ["clock-weather"], {"a": 1}])
def test_bad_screen_id(h, real, sid):
    async def body(p):
        r = await p.preview_idle_screen(sid)
        assert r["ok"] is False and isinstance(r["error"], str) and r["error"]
    h.run(body, wait_connected=False)


def test_screen_without_an_idle_layout(h, real):
    async def body(p):
        r = await p.preview_idle_screen("telemetry")
        assert r == {"ok": False, "error": "telemetry: has no idle layout"}
    h.run(body, wait_connected=False)


@pytest.mark.parametrize("mode", ["rgb", 5, "", "MONO", True, ["mono"], {"m": 1}])
def test_bad_mode(h, real, mode):
    async def body(p):
        r = await p.preview_idle_screen("clock-weather", mode)
        assert r == {"ok": False, "error": "mode must be 'mono' or 'gray4'"}
    h.run(body, wait_connected=False)


def test_mode_the_layout_cannot_be_shown_in(h, real):
    async def body(p):
        await p.import_package(h.package("g.zip", gray_only_zip()))
        assert (await p.list_screens())[0]["id"]   # lists fine
        r = await p.preview_idle_screen("gray-only", "mono")
        assert r["ok"] is False and "cannot be shown in mono mode" in r["error"]
        assert decode(await p.preview_idle_screen("gray-only", "gray4")).mode == "L"
        assert decode(await p.preview_idle_screen("gray-only")).mode == "L"              # default: the only mode that compiles
        await p.set_settings({"idle_screens": ["gray-only"]})
        assert decode(await p.preview_idle_screen("gray-only")).mode == "L"
    h.run(body, wait_connected=False)


def test_a_layout_that_no_longer_loads_is_a_clean_error(h, real):
    async def body(p):
        await p.import_package(h.package("g.zip", gray_only_zip()))
        scr = p.core.store.get("gray-only").path / "screen.json"
        doc = json.loads(scr.read_text())
        doc["idle"]["widgets"][0]["w"] = 9999   # off the panel, edited on disk after the install
        scr.write_text(json.dumps(doc))
        for mode in (None, "gray4", "mono"):
            r = await p.preview_idle_screen("gray-only", mode)
            assert r["ok"] is False and "extends past" in r["error"]
    h.run(body, wait_connected=False)


# -- code packages -----------------------------------------------------------------------------
def test_unapproved_code_package_never_runs_or_renders(h, real, monkeypatch):
    calls = []

    async def body(p):
        store = p.core.store
        monkeypatch.setattr(store, "render", lambda *a, **k: calls.append(a))
        monkeypatch.setattr(plugin_core, "render_declarative", lambda *a, **k: calls.append(a))
        monkeypatch.setattr(preview.subprocess, "run", lambda *a, **k: calls.append(a))
        r = await p.import_package(h.package("c.zip", idle_code_zip()))
        assert r["ok"] and r["needs_approval"]
        r = await p.preview_idle_screen("idle-code")
        assert r["ok"] is False and "approval" in r["error"]
        r = await p.preview_idle_screen("idle-code", "mono")
        assert r["ok"] is False and "approval" in r["error"]
        assert calls == []
    h.run(body, wait_connected=False)


def test_approved_code_package_previews_only_its_idle_layout(h, real, monkeypatch):
    sha = hashlib.sha256(GOOD_CODE.encode()).hexdigest()
    ran = []

    async def body(p):
        await p.import_package(h.package("c.zip", idle_code_zip()))
        assert (await p.approve_package("idle-code", "1.0.0", sha))["ok"]
        monkeypatch.setattr(p.core.store, "render", lambda *a, **k: ran.append(a))   # screen.py must stay untouched
        im = decode(await p.preview_idle_screen("idle-code"))
        assert im.size == (648, 480) and im.mode == "1"
        assert ran == []
        # editing the code revokes the approval again, and the preview stops with it
        (p.core.store.get("idle-code").path / "screen.py").write_text(GOOD_CODE + "\n# changed\n")
        r = await p.preview_idle_screen("idle-code")
        assert r["ok"] is False and "approval" in r["error"]
    h.run(body, wait_connected=False)


# -- the binary --------------------------------------------------------------------------------------
def test_missing_binary_is_a_clean_error(h, tmp_path, monkeypatch):
    monkeypatch.setattr(plugin_core, "IDLE_RENDER_BIN", tmp_path / "nowhere" / "idle_render_host")

    async def body(p):
        assert await p.preview_idle_screen("clock-weather") == {"ok": False, "error": "idle preview renderer is not installed"}
    h.run(body, wait_connected=False)


def test_default_binary_path_is_decky_bin():
    assert plugin_core.IDLE_RENDER_BIN == DECKY / "bin" / "idle_render_host"


def test_runs_a_private_executable_copy_even_when_the_shipped_file_has_no_exec_bit(h, real, tmp_path, monkeypatch):
    noexec = tmp_path / "shipped" / "idle_render_host"
    noexec.parent.mkdir()
    noexec.write_bytes(real.read_bytes())
    noexec.chmod(0o644)   # what Decky's extraction may leave behind
    monkeypatch.setattr(plugin_core, "IDLE_RENDER_BIN", noexec)

    async def body(p):
        assert decode(await p.preview_idle_screen("clock-weather")).size == (648, 480)
        d = h.settings_dir / ".bin"
        copy = d / "idle_render_host"
        assert stat.S_IMODE(d.stat().st_mode) == 0o700 and stat.S_IMODE(copy.stat().st_mode) == 0o700
        assert copy.read_bytes() == real.read_bytes() and os.access(copy, os.X_OK)
        assert os.access(noexec, os.X_OK) is False   # the shipped file itself is never run
        m = copy.stat().st_mtime_ns
        assert decode(await p.preview_idle_screen("clock-weather")).size == (648, 480)
        assert copy.stat().st_mtime_ns == m                    # copied once per core, not per call
        assert [x.name for x in d.iterdir()] == ["idle_render_host"]   # no temp files left behind
    h.run(body, wait_connected=False)


def test_renderer_timeout_is_a_clean_error_and_the_child_is_killed(h, tmp_path, monkeypatch):
    marker = tmp_path / "still-alive"
    fake = script(tmp_path, f"sleep 5; touch '{marker}'")
    monkeypatch.setattr(plugin_core, "IDLE_RENDER_BIN", fake)
    monkeypatch.setattr(preview, "TIMEOUT_S", 0.5)

    async def body(p):
        r = await p.preview_idle_screen("clock-weather")
        assert r == {"ok": False, "error": "idle preview timed out"}
    h.run(body, wait_connected=False)
    import time
    time.sleep(0.2)
    assert not marker.exists()


@pytest.mark.parametrize("body_,msg", [
    ("exit 3", "renderer exit 3"),
    ("kill -SEGV $$", "renderer exit -11"),
    ("exit 0", "unreadable output"),                          # no file written
    ("echo notapng > \"$3\"", "unreadable output"),
    ("head -c 3000000 /dev/zero > \"$3\"", "unexpected output"),
])
def test_renderer_crash_or_bad_output_is_a_clean_error(h, tmp_path, monkeypatch, body_, msg):
    monkeypatch.setattr(plugin_core, "IDLE_RENDER_BIN", script(tmp_path, body_))

    async def body(p):
        r = await p.preview_idle_screen("clock-weather")
        assert r["ok"] is False and msg in r["error"], r
        assert len(r["error"]) < 80
    h.run(body, wait_connected=False)


def test_renderer_output_of_the_wrong_size_is_refused(h, tmp_path, monkeypatch):
    small = tmp_path / "small.png"
    Image.new("1", (10, 10)).save(small)
    monkeypatch.setattr(plugin_core, "IDLE_RENDER_BIN", script(tmp_path, f"cp '{small}' \"$3\""))

    async def body(p):
        r = await p.preview_idle_screen("clock-weather")
        assert r["ok"] is False and "unexpected output size" in r["error"]
    h.run(body, wait_connected=False)


def test_renderer_stderr_is_never_passed_on(h, tmp_path, monkeypatch):
    monkeypatch.setattr(plugin_core, "IDLE_RENDER_BIN", script(tmp_path, f"echo 'leak {WIFI}' >&2; exit 1"))

    async def body(p):
        r = await p.preview_idle_screen("clock-weather")
        assert r["ok"] is False and WIFI not in json.dumps(r) and "leak" not in r["error"]
    h.run(body, wait_connected=False)
    assert WIFI not in h.caplog.text


def test_unexpected_exception_is_caught_at_the_boundary(h, real, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError(f"internal {WIFI}")

    async def body(p):
        monkeypatch.setattr(p.core._idle_renderer, "render", boom)
        r = await p.preview_idle_screen("clock-weather")
        assert r == {"ok": False, "error": "idle preview failed"}
    h.run(body, wait_connected=False)


# -- secrets -----------------------------------------------------------------------------------------
def test_wifi_password_and_steam_key_never_reach_the_renderer_or_the_result(h, real, tmp_path, monkeypatch):
    spool = tmp_path / "spool"
    spool.mkdir()
    gold = GOLD / "clock-weather.png"
    fake = script(tmp_path, f"echo \"$@\" > '{spool}/argv'; cp \"$1\" '{spool}/layout.bin'; cp \"$2\" '{spool}/sample.txt'; cp '{gold}' \"$3\"")
    monkeypatch.setattr(plugin_core, "IDLE_RENDER_BIN", fake)

    async def body(p):
        r = await p.set_settings({"wifi_ssid": "ssid-secret-net", "wifi_pass": WIFI, "steam_api_key": KEY, "steamid64": "76561198000000000",
                                  "idle_screens": ["clock-weather"]})
        assert r["ok"]
        res = await p.preview_idle_screen("clock-weather")
        assert res["ok"], res
        blob = json.dumps(res).encode() + b"".join(f.read_bytes() for f in spool.iterdir())
        for secret in (WIFI, KEY, "76561198000000000", "ssid-secret-net"):
            assert secret.encode() not in blob, secret
        # and in an error path the scrub still holds
        monkeypatch.setattr(plugin_core, "IDLE_RENDER_BIN", tmp_path / "gone")
        assert WIFI not in json.dumps(await p.preview_idle_screen("clock-weather"))
    h.run(body, wait_connected=False)
    assert WIFI not in h.caplog.text and KEY not in h.caplog.text


def test_does_not_need_a_connected_panel_and_does_not_touch_the_engine(h, real):
    async def body(p):
        before = p.core.engine.status()["frames_rendered"]
        assert (await p.preview_idle_screen("minimal-clock"))["ok"]
        assert p.core.engine.status()["frames_rendered"] == before
        assert h.events == [] or "status" in h.names()   # a preview emits nothing of its own
        assert "settings_changed" not in h.names()
    h.plugged = False
    h.run(body, wait_connected=False)


def test_uninstall_purge_removes_the_private_binary_copy(h, real):
    async def body(p):
        assert (await p.preview_idle_screen("clock-weather"))["ok"]
        assert (h.settings_dir / ".bin" / "idle_render_host").is_file()
        await p.set_settings({"purge_on_uninstall": True})
    h.run(body, wait_connected=False)
    assert plugin_core.purge_if_requested(h.settings_dir)
    assert not (h.settings_dir / ".bin").exists()
