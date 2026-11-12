"""Thin Decky wrapper. All logic lives in py_modules/inkterface_core/plugin_core.py (no decky imports there).

Every callable runs the blocking core call in a worker thread, so a slow screen.py or a busy engine lock never
stalls Decky's event loop. See docs/plugin-ui.md for the callables and events.
"""
import asyncio
import json
import logging
from pathlib import Path

import decky
from inkterface_core.engine import usb_connect
from inkterface_core.plugin_core import PluginCore, purge_if_requested
from inkterface_core.sleep_watch import SleepWatcher
from inkterface_core.sources import default_registry

connect = usb_connect  # tests replace these two with an emulator link and an offline registry
registry_factory = default_registry
sleep_watch = SleepWatcher   # logind sleep hint (docs/idle-mode.md); tests set None
firmware_kw: dict = {}   # tests inject fetch/clock/public_key/... for the firmware updater
catalog_kw: dict = {}   # tests inject catalog_fetch/catalog_key for the screen catalog
setup_kw: dict = {}   # tests inject setup_fetch/wifi_run for the Settings > Connections helpers
WATCH_S = 1.0
_STATUS_KEYS = ("ui_state", "current_screen", "pinned", "now_playing", "profile", "fw_version", "last_error", "last_frame_at",
                "reconnects")


def _version() -> str:
    try:
        return json.loads((Path(decky.DECKY_PLUGIN_DIR) / "package.json").read_text("utf-8"))["version"]
    except (OSError, ValueError, KeyError, TypeError):
        return "unknown"


class Plugin:
    core: PluginCore | None = None
    _watch: asyncio.Task | None = None
    _log_handlers: list = ()

    # -- lifecycle ----------------------------------------------------------------
    async def _main(self):
        core_log = logging.getLogger("inkterface_core")  # route the core's logs into Decky's plugin log
        self._log_handlers = list(decky.logger.handlers)
        for h in self._log_handlers:
            core_log.addHandler(h)
        core_log.setLevel(decky.logger.level or logging.INFO)
        loop = asyncio.get_running_loop()

        def firmware_event(status):   # called from the updater's threads
            coro = decky.emit("firmware", status)
            try:
                asyncio.run_coroutine_threadsafe(coro, loop)
            except RuntimeError:   # loop already closed (unload)
                coro.close()

        core = PluginCore(decky.DECKY_PLUGIN_SETTINGS_DIR, Path(decky.DECKY_PLUGIN_DIR) / "screens",
                          connect=lambda: connect(), registry_factory=lambda s: registry_factory(s),
                          version=_version(), on_firmware=firmware_event, sleep_watch=(lambda *a: sleep_watch(*a)) if sleep_watch else None, **firmware_kw, **catalog_kw, **setup_kw)
        await asyncio.to_thread(core.start)
        self.core = core
        self._watch = asyncio.create_task(self._watch_status())
        decky.logger.info("Inkterface loaded")

    async def _unload(self):
        if self._watch:
            self._watch.cancel()
            await asyncio.gather(self._watch, return_exceptions=True)
            self._watch = None
        core, self.core = self.core, None
        if core:
            await asyncio.to_thread(core.shutdown)
        core_log = logging.getLogger("inkterface_core")
        for h in self._log_handlers:
            core_log.removeHandler(h)
        self._log_handlers = ()
        decky.logger.info("Inkterface unloaded")

    async def _uninstall(self):
        """User data (settings, installed packages, the Steam key) stays unless the user turned on purge_on_uninstall."""
        if await asyncio.to_thread(purge_if_requested, decky.DECKY_PLUGIN_SETTINGS_DIR):
            decky.logger.info("Inkterface user data removed")

    # -- events -------------------------------------------------------------------
    async def _watch_status(self):
        """Emit `status` whenever something the UI shows changes (polling: the engine runs in its own thread)."""
        last = None
        while True:
            try:
                st = await asyncio.to_thread(self.core.get_status)
                idle = st["idle"]   # upload progress is UI-visible too
                key = tuple(st[k] for k in _STATUS_KEYS) + (idle["state"], idle["error"], tuple(idle["on_panel"]))
                if key != last:
                    last = key
                    await decky.emit("status", st)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the watcher must outlive one failed poll
                decky.logger.exception("status watcher")
            await asyncio.sleep(WATCH_S)

    async def _call(self, name, *args, events=()):
        if self.core is None:
            raise RuntimeError("Inkterface is not running")
        res = await asyncio.to_thread(getattr(self.core, name), *args)
        if events and isinstance(res, dict) and res.get("ok"):
            for e in events:
                await decky.emit(e)
            await decky.emit("status", await asyncio.to_thread(self.core.get_status))
        return res

    # -- callables (frontend: callable("name")) -------------------------------------
    async def get_status(self):
        return await self._call("get_status")

    async def list_screens(self):
        return await self._call("list_screens")

    async def get_settings(self):
        return await self._call("get_settings")

    async def set_settings(self, patch):
        return await self._call("set_settings", patch, events=("settings_changed",))

    async def set_screen_settings(self, screen_id, values):
        return await self._call("set_screen_settings", screen_id, values, events=("settings_changed",))

    async def pin_screen(self, screen_id, profile=None):
        return await self._call("pin_screen", screen_id, profile, events=("settings_changed",))

    async def unpin_screen(self, profile=None):
        return await self._call("unpin_screen", profile, events=("settings_changed",))

    async def set_rotation(self, screen_ids, interval_s=None, profile=None):
        return await self._call("set_rotation", screen_ids, interval_s, profile, events=("settings_changed",))

    async def set_screen_enabled(self, screen_id, enabled):
        return await self._call("set_screen_enabled", screen_id, enabled, events=("screens_changed",))

    async def set_now_playing(self, appid):
        return await self._call("set_now_playing", appid)

    async def import_package(self, source):
        return await self._call("import_package", source, events=("screens_changed",))

    async def approve_package(self, screen_id, version, sha256):
        return await self._call("approve_package", screen_id, version, sha256, events=("screens_changed",))

    async def uninstall_package(self, screen_id):
        return await self._call("uninstall_package", screen_id, events=("screens_changed", "settings_changed"))

    async def preview_screen(self, screen_id):
        return await self._call("preview_screen", screen_id)

    async def preview_idle_screen(self, screen_id, mode=None):
        return await self._call("preview_idle_screen", screen_id, mode)

    async def get_firmware_status(self):
        return await self._call("get_firmware_status")

    async def check_firmware(self):
        return await self._call("check_firmware")

    async def install_firmware(self, version):
        return await self._call("install_firmware", version)

    async def cancel_firmware_install(self):
        return await self._call("cancel_firmware_install")

    async def push_idle_layouts(self):
        return await self._call("push_idle_layouts")

    async def search_places(self, query, language="en"):
        return await self._call("search_places", query, language)

    async def locate_by_ip(self):
        return await self._call("locate_by_ip")

    async def resolve_steam_id(self, text):
        return await self._call("resolve_steam_id", text)

    async def steam_profile(self, steamid64=None):
        return await self._call("steam_profile", steamid64)

    async def test_steam_key(self):
        return await self._call("test_steam_key")

    async def wifi_current(self):
        return await self._call("wifi_current")

    async def catalog_refresh(self):
        return await self._call("catalog_refresh")

    async def catalog_list(self):
        return await self._call("catalog_list")

    async def catalog_updates(self):
        return await self._call("catalog_updates")

    async def catalog_install(self, screen_id, version):
        return await self._call("catalog_install", screen_id, version, events=("screens_changed",))

    async def catalog_preview(self, screen_id):
        return await self._call("catalog_preview", screen_id)
