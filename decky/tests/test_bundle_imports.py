"""The plugin must load AND run inside Decky Loader's PyInstaller Python, which bundles only the stdlib modules in
decky_3.3.1_stdlib.txt (no termios, no zoneinfo, ...).

A subprocess installs an import hook that refuses every stdlib module outside that list and every non-stdlib module
outside decky/py_modules, then:
  1. imports every module under inkterface_core (walked, no hand-kept list) and main.py (fake `decky` module);
  2. renders every built-in screen, runs engine ticks on the emulator, drives the whole plugin (settings, package
     install + code-screen sandbox, firmware check + install over the emulated OTA panel), so imports hidden on hot
     paths are hit too;
  3. runs packages/_runner.py (the code-screen child) under the same hook.
An import wrapped in try/except ImportError is a fallback, not a failure: the hook records every refusal and the test
only accepts those listed in GUARDED (each has its own both-paths unit test).
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
DECKY = TESTS.parent
PM = DECKY / "py_modules"
BUNDLED = TESTS / "decky_3.3.1_stdlib.txt"
GUARDED = {"zoneinfo"}   # optional stdlib modules Decky lacks; every use is a try/except fallback

HOOK = r'''
import sys
from importlib import machinery
ALLOW = set(open(sys.argv[1]).read().split())
PM, TESTS, DECKY = sys.argv[2:5]
# sys.path: py_modules first, then the stdlib only (no site-packages: PIL and friends must come from py_modules)
sys.path[:] = [PM] + [p for p in sys.path if p and "site-packages" not in p and "dist-packages" not in p]


class Guard:
    refused = []

    def find_spec(self, name, path=None, target=None):
        if name.partition(".")[0] in sys.stdlib_module_names and name not in ALLOW:
            if not any(f.find_spec(name, path) for f in (machinery.BuiltinImporter, machinery.FrozenImporter, machinery.PathFinder)):
                return None   # other-platform probe (msvcrt, winreg, ...): absent here anyway, stdlib handles it
            self.refused.append(name)
            raise ImportError("BUNDLE: stdlib module %r is not in Decky's bundle" % name)
        return None


guard = Guard()
sys.meta_path.insert(0, guard)
'''

DRIVER = HOOK + r'''
import asyncio, hashlib, importlib, importlib.util, json, logging, pkgutil, tempfile, types
from pathlib import Path

sys.path[:0] = [TESTS + "/ota", TESTS + "/engine", TESTS + "/packages"]
PMP, DK = Path(PM), Path(DECKY)
failures = []

# -- 1. every module under inkterface_core + main.py -------------------------------------------
import inkterface_core
for m in pkgutil.walk_packages(inkterface_core.__path__, "inkterface_core."):
    try:
        importlib.import_module(m.name)
    except BaseException as e:
        failures.append("%s: %s: %s" % (m.name, type(e).__name__, e))
decky = types.ModuleType("decky")
decky.logger = logging.getLogger("fake-decky")
tmp = Path(tempfile.mkdtemp())
(tmp / "plugin").mkdir()
(tmp / "plugin" / "screens").symlink_to(DK / "screens")
(tmp / "plugin" / "package.json").write_text((DK / "package.json").read_text())
decky.DECKY_PLUGIN_SETTINGS_DIR = str(tmp / "settings")
decky.DECKY_PLUGIN_DIR = str(tmp / "plugin")
events = []


async def emit(event, *args):
    events.append((event, args))


decky.emit = emit
sys.modules["decky"] = decky
spec = importlib.util.spec_from_file_location("plugin_main", DK / "main.py")
main = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(main)
except BaseException as e:
    failures.append("main.py: %s: %s" % (type(e).__name__, e))
if failures:
    sys.exit("IMPORT OFFENDERS:\n  " + "\n  ".join(failures) + "\nREFUSED=" + json.dumps(sorted(set(guard.refused))))
print("stage imports ok")

# -- 2. every built-in screen renders; a clock with a named timezone; the time source -----------
from inkterface_core.idle.compile import compile_layout
from inkterface_core.idle.store import pack_store
from inkterface_core.screens import DataSnapshot, load_screen, render_frame
from inkterface_core.sources import TimeSource, default_registry
idle_blobs = []
for d in sorted((DK / "screens").iterdir()):
    scr = load_screen(d)
    if scr.active is not None:
        sample = d / "sample-data.json"
        f = render_frame(scr, DataSnapshot(json.loads(sample.read_text()), d))
        assert len(f) == 38880 and any(f), d
        print("stage screen", d.name)
    if scr.idle is not None:  # the idle compiler + store packer under the bundle-only import hook
        idle_blobs.append(compile_layout(scr.idle, assets_dir=str(scr.assets_dir)))
        print("stage idle screen", d.name)
assert len(idle_blobs) >= 4 and len(pack_store(idle_blobs[:8], config={"tz": "CET-1CEST,M3.5.0,M10.5.0/3"})) > 0
import datetime
src = TimeSource("Europe/Berlin").poll()
assert src["time.now"].tzinfo is not None
clock_doc = {"schema": 1, "id": "tz", "name": "tz", "version": "1.0.0", "author": "t", "active": {"widgets": [
    {"type": "clock", "x": 0, "y": 0, "w": 300, "h": 80, "timezone": "Asia/Tokyo", "format": "%H:%M %Z"},
    {"type": "clock", "x": 0, "y": 100, "w": 300, "h": 80, "timezone": "UTC"}]}}
(tmp / "tz").mkdir()
(tmp / "tz" / "screen.json").write_text(json.dumps(clock_doc))
assert any(render_frame(load_screen(tmp / "tz"), {"time.now": datetime.datetime(2026, 1, 1, 12, 0, tzinfo=datetime.timezone.utc)}))
print("stage clock ok")

# -- 3. offline sources -------------------------------------------------------------------------
reg = default_registry({"timezone": "Europe/Berlin"}, root=str(tmp), home=str(tmp), network=False)
reg.poll_due(force=True)
assert "time.now" in reg.snapshot()
print("stage sources ok")

# -- 4. engine ticks against the emulator -------------------------------------------------------
from engine_testlib import Rig
rig = Rig(tmp / "rig")
rig.run(2)
assert rig.st["state"] == "ready" and rig.st["rects_sent"] > 0, rig.st
print("stage engine ok")

# -- 5. the plugin end to end: lifecycle, packages, code sandbox, firmware over the emulated OTA panel --
import ota_helpers as H
from packages_testlib import GOOD_CODE, package_zip
from inkterface_core.emulator import EmulatedPanel
from inkterface_core.engine import EmulatedLink

panel = EmulatedPanel(fw_ver="1.0.0", ota_slot=H.SLOT)
gh = H.FakeGitHub()
image = H.make_image(30_000)
gh.listing([gh.publish("1.2.0", image)])


def connect():
    if panel.ota_committed:
        panel.reboot()
    return EmulatedLink(panel)


main.connect = connect
main.registry_factory = lambda s: default_registry(s, root=str(tmp), home=str(tmp), network=False)
main.firmware_kw = dict(fw_fetch=gh, public_key=lambda: H.PUB, sleep=lambda s: None, handshake_timeout=0.5,
                        confirm_timeout=3.0, tick_s=3600.0)
main._version = lambda: "1.0.0"
main.WATCH_S = 0.05


async def until(pred, timeout=20.0):
    end = asyncio.get_running_loop().time() + timeout
    while not pred():
        assert asyncio.get_running_loop().time() < end, "condition not reached in time"
        await asyncio.sleep(0.02)


def write(name, data):
    p = tmp / name
    p.write_bytes(data)
    return str(p)


async def plugin():
    p = main.Plugin()
    await p._main()
    try:
        await until(lambda: p.core.engine.status()["connected"])
        assert (await p.list_screens()) and (await p.get_status())["connected"]
        await p.set_settings({"timezone": "Europe/Berlin"})
        assert (await p.set_settings({"developer_mode": True}))["ok"]   # zip/URL import is a developer feature
        # a declarative package, then a code package (approval -> runs screen.py in the guarded child process)
        assert (await p.import_package(write("d.zip", package_zip("deco-pkg"))))["ok"]
        r = await p.import_package(write("c.zip", package_zip("code-demo", code=GOOD_CODE)))
        assert r["ok"] and r["needs_approval"], r
        pkg = r["package"]
        assert (await p.approve_package("code-demo", pkg["version"], pkg["code_sha256"]))["ok"]
        assert (await p.pin_screen("code-demo"))["ok"]
        await until(lambda: p.core.engine.status()["current_screen"] == "code-demo" and not p.core.engine.status()["last_error"])
        assert (await p.preview_screen("code-demo")) and (await p.preview_screen("big-clock"))
        # firmware: check, install, confirm
        await until(lambda: p.core.firmware.status()["check"]["at"] is not None)
        assert (await p.check_firmware())["check"]["ok"] is True
        assert await p.install_firmware("1.2.0") == {"ok": True}
        await until(lambda: p.core.get_firmware_status()["install"]["state"] == "done")
        assert panel.installed == image
        await p.uninstall_package("deco-pkg")
    finally:
        await p._unload()


asyncio.run(plugin())
print("stage plugin ok")
print("REFUSED=" + json.dumps(sorted(set(guard.refused))))
'''

RUNNER = HOOK + r'''
import json, runpy
runpy.run_path(PM + "/inkterface_core/packages/_runner.py", run_name="__main__")
print("REFUSED=" + json.dumps(sorted(set(guard.refused))), file=sys.stderr)
'''


def run(code, *, stdin=None):
    return subprocess.run([sys.executable, "-I", "-c", code, str(BUNDLED), str(PM), str(TESTS), str(DECKY)],
                          input=stdin, capture_output=True, text=True, timeout=300)


def refused(text):
    line = next(ln for ln in text.splitlines() if ln.startswith("REFUSED="))
    return set(json.loads(line[len("REFUSED="):]))


def test_whole_plugin_loads_and_runs_with_only_the_decky_bundle():
    r = run(DRIVER)
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
    for stage in ("imports", "clock", "sources", "engine", "plugin"):
        assert f"stage {stage} ok" in r.stdout
    assert refused(r.stdout) <= GUARDED, f"unguarded refused imports: {refused(r.stdout) - GUARDED}"


def test_code_screen_child_runs_with_only_the_decky_bundle():
    req = {"code": "def render(size, data, settings):\n    return b'not a png'\n", "path": "screen.py", "size": [648, 480],
           "data": {}, "settings": {}, "sys_path": [str(PM)], "limits": {"memory_mb": 512, "cpu_s": 5, "fsize_mb": 2}}
    r = run(RUNNER, stdin=json.dumps(req))
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["ok"] is True and out["png"], out   # the runner answered; no refused import killed it
    assert refused(r.stderr) <= GUARDED


def test_the_guard_refuses_a_module_missing_from_the_allowlist(tmp_path):
    """Guards the guard: dropping a module the plugin really uses from the allowlist must fail the audit."""
    short = tmp_path / "short.txt"
    short.write_text("\n".join(m for m in BUNDLED.read_text().split() if m != "hashlib"))
    r = subprocess.run([sys.executable, "-I", "-c", DRIVER, str(short), str(PM), str(TESTS), str(DECKY)],
                       capture_output=True, text=True, timeout=300)
    assert r.returncode != 0 and "hashlib" in r.stderr


def test_every_stdlib_import_in_the_source_is_bundled_or_guarded():
    """Static net for code paths the run above does not reach (a lazy import in a rarely used function)."""
    import ast

    allow = set(BUNDLED.read_text().split())
    bad = {}
    for f in [*(PM / "inkterface_core").rglob("*.py"), DECKY / "main.py"]:
        for n in ast.walk(ast.parse(f.read_text())):
            names = [a.name for a in n.names] if isinstance(n, ast.Import) else \
                [n.module] if isinstance(n, ast.ImportFrom) and n.level == 0 and n.module else []
            for x in names:
                if x.partition(".")[0] in sys.stdlib_module_names and x not in allow and x not in GUARDED:
                    bad.setdefault(x, []).append(f.name)
    assert not bad, f"stdlib modules missing from Decky's bundle: {bad}"
