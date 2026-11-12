"""Harness: main.py against a fake `decky` module, an emulated panel and an offline data registry."""
import asyncio
import importlib.util
import json
import logging
import shutil
import sys
import threading
import time
import types
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

DECKY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages"))   # packages_testlib

from inkterface_core.emulator import EmulatedPanel  # noqa: E402
from inkterface_core.engine import EmulatedLink  # noqa: E402
from inkterface_core.sources import Registry, TimeSource  # noqa: E402
from inkterface_core.sources.base import Source  # noqa: E402


class Fixed(Source):
    """Static data so previews are deterministic and nothing reads /proc or the network."""
    name = "fixed"
    interval = 1.0
    paths = ("system.cpu.load", "system.gpu.load")

    def poll(self):
        return {"system.cpu.load": 12.0, "system.gpu.load": 34.0}


class FakeRegistry(Registry):
    def __init__(self, settings):
        super().__init__([TimeSource(), Fixed()])
        self.cfg, self.stopped, self.np = settings, 0, []

    def set_now_playing(self, appid):
        self.np.append(appid)

    def stop(self, timeout=5.0):
        self.stopped += 1
        super().stop(timeout)


class Harness:
    def __init__(self, tmp_path, monkeypatch, caplog):
        self.tmp = tmp_path
        self.settings_dir = tmp_path / "settings"
        self.plugin_dir = tmp_path / "plugin"
        self.plugin_dir.mkdir()
        (self.plugin_dir / "screens").symlink_to(DECKY / "screens")
        shutil.copy(DECKY / "package.json", self.plugin_dir / "package.json")
        self.events = []
        self.panel = EmulatedPanel(fw_ver="emu-fw")
        self.links, self.registries = [], []
        self.plugged = True
        self.logger = logging.getLogger("fake-decky")
        self.logger.setLevel(logging.DEBUG)
        self.decky = types.ModuleType("decky")
        self.decky.logger = self.logger
        self.decky.DECKY_PLUGIN_SETTINGS_DIR = str(self.settings_dir)
        self.decky.DECKY_PLUGIN_DIR = str(self.plugin_dir)

        async def emit(event, *args):
            self.events.append((event, args))

        self.decky.emit = emit
        monkeypatch.setitem(sys.modules, "decky", self.decky)
        spec = importlib.util.spec_from_file_location("plugin_main", DECKY / "main.py")
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)
        self.mod.connect = self._connect
        self.mod.sleep_watch = None   # never touch the real system bus from a test (test_sleep_idle.py injects a fake)
        self.mod.registry_factory = self._registry
        self.mod.WATCH_S = 0.05
        self.mod.catalog_kw = {}   # a catalog test sets this via use_catalog() before run()
        self.use_setup()           # never reach the real network or the host's NetworkManager from a test
        self.caplog = caplog
        caplog.set_level(logging.DEBUG)

    def _connect(self):
        if not self.plugged:
            raise FileNotFoundError("no panel found (USB VID 303a)")
        link = EmulatedLink(self.panel)
        self.links.append(link)
        return link

    def _registry(self, settings):
        r = FakeRegistry(dict(settings))
        self.registries.append(r)
        return r

    #: Developer mode is off by default in production, but importing a zip/URL now requires it; the package and
    #: idle tests exercise import_package, so the harness turns it on unless a test opts out (dev_mode=False).
    dev_mode = True

    @asynccontextmanager
    async def started(self):
        p = self.mod.Plugin()
        await p._main()
        try:
            if self.dev_mode:
                p.core.host.developer_mode = True   # import_package gate; set in-memory so host.json is untouched
            yield p
        finally:
            await p._unload()

    def run(self, body, wait_connected=True):
        """Run `async body(plugin)` with a started plugin (panel connected unless told otherwise)."""
        async def go():
            async with self.started() as p:
                if wait_connected:
                    await self.until(lambda: p.core.engine.status()["connected"])
                return await body(p)
        return asyncio.run(go())

    @staticmethod
    async def until(pred, timeout=10.0):
        end = time.monotonic() + timeout
        while not pred():
            assert time.monotonic() < end, "condition not reached in time"
            await asyncio.sleep(0.02)

    def names(self, *events):
        return [e for e, _ in self.events if not events or e in events]

    def package(self, name, data):
        p = self.tmp / name
        p.write_bytes(data)
        return str(p)

    @property
    def leaked_threads(self):
        return [t.name for t in threading.enumerate() if t.name.startswith(("inkterface-", "src-"))]

    def host_file(self):
        return json.loads((self.settings_dir / "host.json").read_text())

    def use_setup(self, fetch=None, wifi=None):
        """Inject the Settings > Connections helpers' HTTP fetch and nmcli runner (main.setup_kw). Default: both refuse."""
        def no_net(url, timeout):
            raise AssertionError("a test reached the network")

        def no_nmcli(argv):
            raise AssertionError("a test ran nmcli")
        self.mod.setup_kw = {"setup_fetch": fetch or no_net, "wifi_run": wifi or no_nmcli}

    def use_catalog(self, fetch, key):
        """Inject the catalog's fetch and public key through the main.catalog_kw code seam (tests only)."""
        self.mod.catalog_kw = {"catalog_fetch": fetch, "catalog_key": (lambda: key)}


@pytest.fixture
def h(tmp_path, monkeypatch, caplog):
    before = {t.name for t in threading.enumerate()}
    harness = Harness(tmp_path, monkeypatch, caplog)
    yield harness
    leaked = [t for t in harness.leaked_threads if t not in before]
    assert not leaked, f"leaked threads: {leaked}"
