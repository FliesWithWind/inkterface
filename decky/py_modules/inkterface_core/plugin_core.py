"""Everything the Decky plugin does, without importing decky: wires Registry + Engine + PackageStore.

`main.py` is a thin async shell around `PluginCore`. Every user-facing method returns plain JSON data and
never raises for bad input: mutations return {"ok": True, ...} or {"ok": False, "error": "..."}.
See docs/plugin-ui.md. Secrets (the Steam Web API key) live only in `host.json` (mode 0600), are never
logged and are never returned: get_settings() says "set" or "unset".
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
import shutil
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import proto as P
from .catalog import Catalog, CatalogError
from .catalog import trust as catalog_trust
from .catalog.client import DEFAULT_URL as CATALOG_DEFAULT_URL
from .catalog.client import is_newer
from .firmware import FirmwareManager
from .ota import github
from .engine import Engine, Settings, usb_connect
from .idle.preview import IdleRenderer, PreviewError, load_sample
from .idle.plan import MAX_ROTATE_S, MIN_ROTATE_S, MODES, IdlePlan, build_plan, supported_modes
from .packages import PackageError, PackageStore
from .packages.fetch import urllib_fetch
from .screens import ScreenError, load_screen, pack
from .screens import render as render_declarative
from .screens import render_frame
from .screens.schema import ID_RE
from . import setup_tools
from .sources import default_registry, parse_appid
from .sources.netfetch import urllib_fetch as setup_urllib_fetch
from .tz import posix_tz

log = logging.getLogger(__name__)

SETTINGS_FILE, HOST_FILE, SCREENS_DIR = "settings.json", "host.json", "screens"
BIN_DIR = ".bin"   # private 0700 copy of the idle renderer (Decky's zip extraction may drop the exec bit)
# Static x86_64 build of the firmware's idle renderer, shipped in the zip (decky/scripts/build_idle_render.sh). Tests patch it.
IDLE_RENDER_BIN = Path(__file__).resolve().parents[2] / "bin" / "idle_render_host"
UI_STATE = {"disconnected": "waiting", "handshake": "connecting", "ready": "connected", "refused": "firmware_too_old",
            "stopped": "stopped", "paused": "updating"}
# Settings the generic set_settings() may change. rotation / pinned / screen_settings have their own callables.
ENGINE_KEYS = {"rotation_interval_s", "idle_timeout_s", "ghost_clear_minutes", "auto_now_playing", "now_playing_screen", "ingame_enabled"}
PROFILES = (None, "normal", "ingame")   # rotation / pin callables: None = "normal"


def _profile_of(d: dict, profile) -> dict:
    """The dict holding rotation / rotation_interval_s / pinned for `profile` inside a settings dict. The in-game one
    is created as a copy of the normal one the first time it is needed."""
    if profile != "ingame":
        return d
    if d.get("ingame") is None:
        d["ingame"] = {"rotation": list(d["rotation"]), "rotation_interval_s": d["rotation_interval_s"], "pinned": d["pinned"]}
    return d["ingame"]


def _profiles(d: dict) -> list:
    """Every profile dict that exists in a settings dict."""
    return [d] + ([d["ingame"]] if d.get("ingame") else [])
HOST_KEYS = {"weather_lat", "weather_lon", "weather_place", "units", "steamid64", "steam_api_key", "idle_screens", "purge_on_uninstall",
             "idle_modes", "idle_rotate_s", "wifi_ssid", "wifi_pass", "developer_mode", "catalog_url"}
# a change to any of these rebuilds the panel's idle store (the engine uploads it when the bytes differ)
IDLE_KEYS = {"weather_lat", "weather_lon", "units", "steamid64", "steam_api_key", "idle_screens", "idle_modes",
             "idle_rotate_s", "wifi_ssid", "wifi_pass"}
SOURCE_KEYS = {"weather_lat", "weather_lon", "units", "steamid64", "steam_api_key"}  # a change rebuilds the registry
_KEY_RE = re.compile(r"[0-9A-Fa-f]{32}")
MAX_CATALOG_URL = 300
DEV_OFF_MSG = "Importing a zip or link is a developer feature. Turn on Developer mode in Settings first."


def normalize_catalog_url(url: str) -> str:
    """"" -> default. Else add a missing trailing '/'. (Validation is HostConfig.validate.)"""
    if url == "":
        return CATALOG_DEFAULT_URL
    return url if url.endswith("/") else url + "/"


def _catalog_url_ok(url) -> bool:
    if not isinstance(url, str) or url == "":
        return isinstance(url, str)   # "" is valid (resets to default)
    if len(url) > MAX_CATALOG_URL or not url.startswith("https://"):
        return False
    if any(ord(c) < 0x20 or ord(c) == 0x7F or c.isspace() for c in url):
        return False
    rest = url[len("https://"):]
    host = rest.split("/", 1)[0]
    return "@" not in host   # no userinfo (user:pass@host)


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) != float("inf")


@dataclass
class HostConfig:
    """Plugin settings the engine's Settings does not know. host.json, 0600 because it holds the Steam key."""
    weather_lat: float | None = None
    weather_lon: float | None = None
    weather_place: str = ""                            # the name the user picked ("Kraków, Poland"); "" for raw coordinates
    units: str = "metric"
    steamid64: str = ""
    steam_api_key: str = ""
    idle_screens: list = field(default_factory=list)   # screen ids, in the order the panel shows them
    idle_modes: dict = field(default_factory=dict)     # id -> 'mono' | 'gray4' (absent = what the screen declares)
    idle_rotate_s: int = 60                            # seconds per idle screen when several are selected; 0 = stay on the first
    wifi_ssid: str = ""                                # the panel's own Wi-Fi for weather / Steam while idle
    wifi_pass: str = ""                                # secret: goes to the panel only
    purge_on_uninstall: bool = False
    developer_mode: bool = False                       # gate on import_package (zip/URL import is a developer feature)
    catalog_url: str = ""                              # "" = the built-in default; else an https:// base URL

    @staticmethod
    def validate(data) -> list[str]:
        """Problems in a dict of host settings. Messages never contain the offending value (it may be a secret)."""
        if not isinstance(data, dict):
            return ["top level must be an object"]
        errs = [f"{k}: unknown setting" for k in sorted(set(data) - set(HostConfig.__dataclass_fields__))]
        lat, lon = data.get("weather_lat"), data.get("weather_lon")
        if lat is not None and not (_num(lat) and -90 <= lat <= 90):
            errs.append("weather_lat: must be a number from -90 to 90")
        if lon is not None and not (_num(lon) and -180 <= lon <= 180):
            errs.append("weather_lon: must be a number from -180 to 180")
        if (lat is None) != (lon is None):
            errs.append("weather_lat / weather_lon: set both or neither")
        place = data.get("weather_place", "")
        if not (isinstance(place, str) and len(place) <= 120 and not any(ord(c) < 0x20 or ord(c) == 0x7F for c in place)):
            errs.append("weather_place: must be text of at most 120 characters")
        if data.get("units", "metric") not in ("metric", "imperial"):
            errs.append("units: must be 'metric' or 'imperial'")
        sid = data.get("steamid64", "")
        if not (isinstance(sid, str) and (sid == "" or (sid.isascii() and sid.isdigit() and len(sid) == 17))):
            errs.append("steamid64: must be empty or 17 digits")
        key = data.get("steam_api_key", "")
        if not (isinstance(key, str) and (key == "" or _KEY_RE.fullmatch(key))):
            errs.append("steam_api_key: must be empty or 32 hexadecimal characters")
        idle = data.get("idle_screens", [])
        if not (isinstance(idle, list) and all(isinstance(i, str) and ID_RE.fullmatch(i) for i in idle)
                and len(set(idle)) == len(idle) and len(idle) <= 16):
            errs.append("idle_screens: must be a list of up to 16 unique screen ids")
        modes = data.get("idle_modes", {})
        if not (isinstance(modes, dict) and all(isinstance(k, str) and ID_RE.fullmatch(k) and v in MODES for k, v in modes.items())):
            errs.append("idle_modes: must map screen ids to 'mono' or 'gray4'")
        rs = data.get("idle_rotate_s", 60)
        if not (isinstance(rs, int) and not isinstance(rs, bool) and (rs == 0 or MIN_ROTATE_S <= rs <= MAX_ROTATE_S)):
            errs.append(f"idle_rotate_s: must be 0 (no rotation) or {MIN_ROTATE_S} to {MAX_ROTATE_S}")
        ssid, pw = data.get("wifi_ssid", ""), data.get("wifi_pass", "")
        if not (isinstance(ssid, str) and len(ssid.encode()) <= 32 and not any(ord(c) < 0x20 or ord(c) == 0x7F for c in ssid)):
            errs.append("wifi_ssid: must be text of at most 32 bytes")
        if not (isinstance(pw, str) and len(pw.encode()) <= 63 and not any(ord(c) < 0x20 or ord(c) == 0x7F for c in pw)):
            errs.append("wifi_pass: must be text of at most 63 bytes")
        if not isinstance(data.get("purge_on_uninstall", False), bool):
            errs.append("purge_on_uninstall: must be true or false")
        if not isinstance(data.get("developer_mode", False), bool):
            errs.append("developer_mode: must be true or false")
        if not _catalog_url_ok(data.get("catalog_url", "")):
            errs.append("catalog_url: must be empty or an https:// URL (max 300 chars, no spaces or userinfo)")
        return errs

    @classmethod
    def load(cls, path: Path) -> tuple["HostConfig", str | None]:
        """(config, error). A bad file is moved to host.json.corrupt (it may hold the key) and defaults are used."""
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return cls(), None
        except OSError as e:
            return cls(), f"cannot read {path.name}: {e.strerror or e}"
        try:
            data = json.loads(raw.decode("utf-8"))
            errs = cls.validate(data)
            if errs:
                raise ValueError("; ".join(errs))
            return cls(**data), None
        except (ValueError, UnicodeDecodeError) as e:
            try:
                os.replace(path, str(path) + ".corrupt")
            except OSError:
                pass
            return cls(), f"{path.name}: {e}; using defaults"

    def save(self, path: Path) -> None:
        """Atomic, and 0600 from the first byte (mkstemp) so the key is never world-readable."""
        os.makedirs(path.parent, mode=0o700, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".host-", suffix=".tmp", dir=path.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(asdict(self), f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def source_settings(self) -> dict:
        return {"weather_lat": self.weather_lat, "weather_lon": self.weather_lon, "units": self.units,
                "steamid64": self.steamid64 or None, "steam_api_key": self.steam_api_key or None}


def purge_if_requested(settings_dir) -> bool:
    """Plugin uninstall: delete our user data only when the user asked for it (purge_on_uninstall)."""
    d = Path(settings_dir)
    cfg, _ = HostConfig.load(d / HOST_FILE)
    if not cfg.purge_on_uninstall:
        return False
    for name in (SETTINGS_FILE, SETTINGS_FILE + ".corrupt", HOST_FILE, HOST_FILE + ".corrupt"):
        (d / name).unlink(missing_ok=True)
    shutil.rmtree(d / SCREENS_DIR, ignore_errors=True)
    shutil.rmtree(d / BIN_DIR, ignore_errors=True)
    return True


def _fail(msg) -> dict:
    return {"ok": False, "error": msg if isinstance(msg, str) else "; ".join(msg)}


class PluginCore:
    def __init__(self, settings_dir, builtin_dir, *, connect=usb_connect, registry_factory=default_registry,
                 fetch=urllib_fetch, version: str = "unknown", firmware_factory=FirmwareManager,
                 fw_fetch=github.urllib_fetch, on_firmware=None, sleep_watch=None,
                 catalog_fetch=None, catalog_key=catalog_trust.public_key, setup_fetch=setup_urllib_fetch,
                 wifi_run=setup_tools.run_nmcli, **fw_kw):
        """sleep_watch(on_sleep, on_wake) -> object with start()/stop(): the logind sleep hint (sleep_watch.SleepWatcher). None = off.
        catalog_fetch / catalog_key are the catalog's injection seam (tests only): a fetch(url,max_bytes,timeout) and the
        public-key source. Default key is the empty production key, so a release build has no catalog key."""
        self.dir, self.builtin_dir, self.version = Path(settings_dir), Path(builtin_dir), version
        self._connect, self._factory, self._fetch = connect, registry_factory, fetch
        self._fw_args = (firmware_factory, fw_fetch, on_firmware, fw_kw)
        self._catalog_fetch, self._catalog_key = catalog_fetch, catalog_key
        self._setup_fetch, self._wifi_run = setup_fetch, wifi_run   # Settings > Connections helpers (tests inject fakes)
        self._sleep_factory = sleep_watch
        self.sleep_hint = None
        self._idle_renderer = IdleRenderer(self.dir / BIN_DIR)
        self.firmware: FirmwareManager | None = None
        self._lock = threading.RLock()
        self.engine: Engine | None = None
        self.store: PackageStore | None = None
        self.catalog: Catalog | None = None
        self.host = HostConfig()
        self.host_error: str | None = None

    # -- lifecycle ----------------------------------------------------------------
    def start(self) -> None:
        """Load settings, start data sources and the engine. On failure everything started is stopped again."""
        with self._lock:
            if self.engine is not None:
                return
            self.host, self.host_error = HostConfig.load(self.dir / HOST_FILE)
            settings, settings_error = Settings.load(self.dir / SETTINGS_FILE)
            registry = None
            try:
                self.store = PackageStore(self.dir / SCREENS_DIR, self.builtin_dir, fetch=self._fetch)
                self.catalog = self._make_catalog()
                screens, errors = self._active_screens()
                registry = self._factory(self.host.source_settings())
                registry.start()
                self.engine = Engine(registry, screens, settings, self._connect, settings_error=settings_error,
                                     screen_errors=errors, render=self._render)
                self.engine.set_idle_plan(self._idle_plan())
                self.engine.start()
                make, fw_fetch, on_fw, fw_kw = self._fw_args
                self.firmware = make(self._need, self._connect, plugin_version=self.version, fetch=fw_fetch,
                                     on_event=on_fw, **{"cache_path": self.dir / "cache" / "fw-releases.json", **fw_kw})
                self.firmware.start()
                if self._sleep_factory is not None:
                    self.sleep_hint = self._sleep_factory(self.engine.prepare_sleep, self.engine.wake)
                    self.sleep_hint.start()
            except BaseException:
                hint, self.sleep_hint = self.sleep_hint, None
                if hint is not None:
                    hint.stop()
                fw, self.firmware = self.firmware, None
                if fw is not None:
                    fw.stop()
                engine, self.engine = self.engine, None
                if engine is not None:
                    engine.stop()
                if registry is not None:
                    registry.stop()
                raise
            log.info("inkterface started: %d screens, settings %s", len(screens), "defaulted" if settings_error else "loaded")

    def shutdown(self) -> None:
        """Stop the engine (hands the panel back with MODE PANEL), then the data threads. Safe to call twice."""
        with self._lock:
            hint, self.sleep_hint = self.sleep_hint, None
            if hint is not None:
                hint.stop()   # releases the logind lock first: a suspend must not wait for a plugin that is leaving
            fw, self.firmware = self.firmware, None
            if fw is not None:
                fw.stop()   # cancels a running install and hands the port back before the engine stops
            engine, self.engine = self.engine, None
            if engine is None:
                return
            try:
                engine.stop()
            finally:
                engine.registry.stop()

    # -- helpers ------------------------------------------------------------------
    def _need(self) -> Engine:
        if self.engine is None:
            raise RuntimeError("plugin is not running")
        return self.engine

    def _make_catalog(self) -> Catalog:
        """Build the catalog client for the current catalog_url. One injectable fetch; the key source is the seam."""
        kw = {"public_key": self._catalog_key}
        if self._catalog_fetch is not None:
            kw["fetch"] = self._catalog_fetch
        return Catalog(self.dir / "catalog", normalize_catalog_url(self.host.catalog_url), **kw)

    def _render(self, screen, snapshot, overrides) -> bytes:
        """Engine render hook: code screens go through the store (approval check + child process)."""
        if screen.code_path is None:
            return render_frame(screen, snapshot, overrides)
        return pack(self.store.render(screen.id, snapshot, overrides))

    def _active_screens(self) -> tuple[dict, list[str]]:
        """{id: Screen} the engine may show: enabled (and approved) packages that have an active section."""
        out, errs = {}, []
        for info in self.store.list():
            if not info.enabled:
                continue
            try:
                s = load_screen(info.path, info.id)
            except ScreenError as e:
                errs.append(f"{info.id}: {e.errors[0]}")
                continue
            if s.active is not None:
                out[s.id] = s
        return out, errs

    def _sync_screens(self) -> None:
        screens, errs = self._active_screens()
        self._need().set_screens(screens, errs)
        self._sync_idle()

    def _idle_plan(self) -> IdlePlan:
        """The idle store for the selected idle screens. Never raises: a screen that does not compile is `plan.error`."""
        h, infos, chosen, problems = self.host, {i.id: i for i in self.store.list()}, [], []
        for sid in h.idle_screens:
            try:
                if sid not in infos:
                    raise ScreenError(f"{sid}: not installed")
                scr = load_screen(infos[sid].path, sid)
                if scr.idle is None:
                    raise ScreenError(f"{sid}: has no idle layout")
                chosen.append((sid, scr))
            except ScreenError as e:
                problems.append(e.errors[0])
        if problems:
            return IdlePlan(ids=tuple(h.idle_screens), error="; ".join(problems))
        return build_plan(chosen, modes=h.idle_modes, rotate_s=h.idle_rotate_s, tz=posix_tz(), lat=h.weather_lat,
                          lon=h.weather_lon, units=h.units, steam_key=h.steam_api_key, steam_id=h.steamid64,
                          wifi_ssid=h.wifi_ssid, wifi_pass=h.wifi_pass)

    def _sync_idle(self, force: bool = False) -> None:
        eng = self.engine
        if eng is not None:
            eng.set_idle_plan(self._idle_plan(), force)

    def _scrub(self, text, *also):
        """Defence in depth: the key must not leave in an error string even if some library echoed it."""
        for k in (self.host.steam_api_key, self.host.wifi_pass, *also):
            if k and isinstance(text, str):
                text = text.replace(k, "***")
        return text

    def _save_engine_settings(self, mutate) -> dict:
        """Copy the engine settings, mutate the dict, validate, persist, apply. One error string or ok."""
        eng = self._need()
        d = eng.settings.to_dict()
        mutate(d)
        errs = Settings.validate(d)
        if errs:
            return _fail(errs)
        known = {i.id for i in self.store.list()}
        before = {i for p in _profiles(eng.settings.to_dict()) for i in (*p["rotation"], p["pinned"])}
        for i in [i for p in _profiles(d) for i in (*p["rotation"], p["pinned"])]:   # only ids this change adds: a stale id from an old file must not block edits
            if i is not None and i not in before and i not in known:
                return _fail(f"unknown screen '{i}'")
        s = Settings.from_dict(d)
        try:
            s.save(self.dir / SETTINGS_FILE)
        except OSError as e:
            return _fail(f"cannot save settings: {e.strerror or e}")
        eng.update_settings(s)
        return {"ok": True}

    def _pkg(self, sid):
        if not isinstance(sid, str):
            raise PackageError("screen id must be a string")
        return self.store.get(sid)

    def _usable(self, sid) -> str | None:
        """Error text unless `sid` is an enabled screen the engine can show."""
        if not isinstance(sid, str):
            return "screen id must be a string"
        if sid not in self._need().screens:
            return f"'{sid}' is not an enabled active screen"
        return None

    def _info(self, info) -> dict:
        d = {"id": info.id, "name": info.name, "version": info.version, "author": info.author,
             "description": info.description, "builtin": info.builtin, "enabled": info.enabled,
             "has_code": info.has_code, "code_sha256": info.code_sha256, "approved": info.approved,
             "needs_approval": info.needs_approval, "has_active": False, "has_idle": False, "refresh_s": None,
             "settings_schema": [], "settings": {}}
        try:
            s = load_screen(info.path, info.id)
        except ScreenError as e:
            d["error"] = e.errors[0]
            return d
        d.update(has_active=s.active is not None, has_idle=s.idle is not None, settings_schema=s.settings,
                 refresh_s=s.refresh_s if s.active else None)
        if s.idle is not None:   # modes the panel layout compiles in; "idle_mode" is what would be uploaded
            d["idle_modes"] = supported_modes(s.idle, s.assets_dir)
            d["idle_mode"] = self.host.idle_modes.get(info.id) or s.idle.get("render_mode", "mono")
        over = self._need().settings.screen_settings.get(info.id)
        try:
            d["settings"] = s.resolve_settings(over)
        except ScreenError as e:
            d["settings"] = s.resolve_settings(None)
            d["error"] = e.errors[0]
        return d

    # -- reads --------------------------------------------------------------------
    def get_status(self) -> dict:
        eng = self._need()
        st = eng.status()
        st["ui_state"] = UI_STATE.get(st["state"], st["state"])
        st["idle_supported"] = bool(st["connected"] and (st["caps"] or 0) & P.CAP_IDLE_STORAGE)
        if st["idle"].get("error"):
            st["idle"]["error"] = self._scrub(st["idle"]["error"])
        st["plugin_version"] = self.version
        st["host_error"] = self.host_error
        st["last_error"] = self._scrub(st["last_error"])
        return st

    def list_screens(self) -> list:
        eng = self._need()
        cur, pinned, rot = eng.status()["current_screen"], eng.settings.pinned, list(eng.settings.rotation)
        if eng.settings.ingame_enabled:   # "in Live" (Store badges, uninstall warning) means in either rotation
            rot += eng.settings.profile("ingame")["rotation"]
        out = []
        for info in self.store.list():
            d = self._info(info)
            d.update(current=info.id == cur, pinned=info.id == pinned, in_rotation=info.id in rot,
                     in_idle=info.id in self.host.idle_screens)
            out.append(d)
        return out

    def get_settings(self) -> dict:
        eng = self._need()
        d = {"ingame_enabled": False, "ingame": None, **eng.settings.to_dict()}
        d.update(weather_lat=self.host.weather_lat, weather_lon=self.host.weather_lon, weather_place=self.host.weather_place,
                 units=self.host.units,
                 steamid64=self.host.steamid64, steam_api_key="set" if self.host.steam_api_key else "unset",
                 idle_screens=list(self.host.idle_screens), idle_modes=dict(self.host.idle_modes),
                 idle_rotate_s=self.host.idle_rotate_s, wifi_ssid=self.host.wifi_ssid,
                 wifi_pass="set" if self.host.wifi_pass else "unset", purge_on_uninstall=self.host.purge_on_uninstall,
                 developer_mode=self.host.developer_mode, catalog_url=self.host.catalog_url,
                 catalog_url_default=CATALOG_DEFAULT_URL)
        d["errors"] = [e for e in (eng.status()["settings_error"], self.host_error) if e]
        return d

    # -- Settings > Connections helpers ---------------------------------------------
    def _setup(self, name, fn) -> dict:
        """Run one helper: never raises, and the error text never holds the Steam key or the Wi-Fi password."""
        try:
            r = fn()
        except Exception as e:  # noqa: BLE001 - these are best-effort conveniences; the settings page must not break
            log.warning("%s failed: %s", name, type(e).__name__)
            return _fail("Something went wrong. Try again, or type the value in.")
        if not r.get("ok"):
            r = _fail(self._scrub(r.get("error", "failed")))
        return r

    def search_places(self, query, language="en") -> dict:
        return self._setup("search_places", lambda: setup_tools.search_places(self._setup_fetch, query, language))

    def locate_by_ip(self) -> dict:
        return self._setup("locate_by_ip", lambda: setup_tools.locate_by_ip(self._setup_fetch))

    def resolve_steam_id(self, text) -> dict:
        return self._setup("resolve_steam_id", lambda: setup_tools.resolve_steam_id(self._setup_fetch, text, self.host.steam_api_key))

    def steam_profile(self, steamid64=None) -> dict:
        sid = self.host.steamid64 if steamid64 is None else steamid64
        return self._setup("steam_profile", lambda: setup_tools.steam_profile(self._setup_fetch, sid, self.host.steam_api_key))

    def test_steam_key(self) -> dict:
        """Asks Steam about the SAVED account with the SAVED key: the key never travels to the UI and back."""
        return self._setup("test_steam_key", lambda: setup_tools.check_steam_key(self._setup_fetch, self.host.steam_api_key, self.host.steamid64))

    def wifi_current(self) -> dict:
        return self._setup("wifi_current", lambda: setup_tools.wifi_current(self._wifi_run))

    # -- settings -----------------------------------------------------------------
    def set_settings(self, patch) -> dict:
        """Global settings. All-or-nothing: any invalid key changes nothing."""
        with self._lock:
            eng = self._need()
            if not isinstance(patch, dict) or not patch:
                return _fail("settings patch must be a non-empty object")
            bad = sorted(set(patch) - ENGINE_KEYS - HOST_KEYS)
            if bad:
                return _fail(f"{', '.join(bad)}: not a global setting (rotation, pin and per-screen settings have their own calls)")
            e_patch = {k: v for k, v in patch.items() if k in ENGINE_KEYS}
            h_patch = {k: v for k, v in patch.items() if k in HOST_KEYS}
            if "catalog_url" in h_patch and _catalog_url_ok(h_patch["catalog_url"]):
                # store it normalised ("" stays "" = default; else a trailing "/" is added)
                h_patch["catalog_url"] = "" if h_patch["catalog_url"] == "" else normalize_catalog_url(h_patch["catalog_url"])
            if ("weather_lat" in h_patch or "weather_lon" in h_patch) and "weather_place" not in h_patch:
                # a place name belongs to the coordinates it was picked for: moving them by hand drops it
                if (h_patch.get("weather_lat", self.host.weather_lat), h_patch.get("weather_lon", self.host.weather_lon)) != (self.host.weather_lat, self.host.weather_lon):
                    h_patch["weather_place"] = ""
            host = HostConfig(**{**asdict(self.host), **h_patch})
            errs = HostConfig.validate(asdict(host))
            if errs:
                return _fail(errs)
            known = {i.id: i for i in self.store.list()}
            for sid in h_patch.get("idle_screens", []):
                if sid not in known or not self._info(known[sid])["has_idle"]:
                    return _fail(f"idle_screens: '{sid}' is not an installed screen with an idle layout")
            for sid, mode in h_patch.get("idle_modes", {}).items():
                if sid not in known or mode not in (self._info(known[sid]).get("idle_modes") or ()):
                    return _fail(f"idle_modes: '{sid}' cannot be shown in {mode} mode")
            if e_patch:
                dry = eng.settings.to_dict()
                dry.update(e_patch)
                errs = Settings.validate(dry)
                if errs:
                    return _fail(errs)
                if "now_playing_screen" in e_patch and e_patch["now_playing_screen"] not in known:
                    return _fail(f"now_playing_screen: unknown screen '{e_patch['now_playing_screen']}'")
            registry = None
            if h_patch:
                if SOURCE_KEYS & set(h_patch) and any(getattr(host, k) != getattr(self.host, k) for k in SOURCE_KEYS):
                    try:
                        registry = self._factory(host.source_settings())
                    except Exception as e:  # noqa: BLE001 - a source constructor must not wedge the settings page
                        return _fail(self._scrub(f"cannot apply source settings: {type(e).__name__}: {e}", host.steam_api_key))
                try:
                    host.save(self.dir / HOST_FILE)
                except OSError as e:
                    return _fail(f"cannot save settings: {e.strerror or e}")
            if e_patch:
                def mutate(d):
                    d.update(e_patch)
                    if d.get("ingame_enabled"):
                        _profile_of(d, "ingame")   # first time on: the in-game profile starts as a copy of the normal one

                r = self._save_engine_settings(mutate)
                if not r["ok"]:
                    return r
            if h_patch:
                url_changed = "catalog_url" in h_patch and host.catalog_url != self.host.catalog_url
                self.host = host
                if url_changed:
                    self.catalog = self._make_catalog()   # point at the new base URL (cache dir is shared)
                if registry is not None:
                    registry.start()
                    old = eng.registry
                    eng.set_registry(registry)
                    old.stop()
                if IDLE_KEYS & set(h_patch):
                    self._sync_idle()
            log.info("settings changed: %s", ", ".join(sorted(patch)))  # key names only, never values
            return {"ok": True, "settings": self.get_settings()}

    def set_screen_settings(self, sid, values) -> dict:
        with self._lock:
            eng = self._need()
            if not isinstance(values, dict):
                return _fail("values must be an object")
            try:
                screen = load_screen(self._pkg(sid).path, sid)
                resolved = screen.resolve_settings(values)
            except ScreenError as e:   # PackageError is a ScreenError
                return _fail(e.errors)

            def mutate(d):
                if values:
                    d["screen_settings"][sid] = dict(values)
                else:
                    d["screen_settings"].pop(sid, None)

            r = self._save_engine_settings(mutate)
            return {**r, "settings": resolved} if r["ok"] else r

    def pin_screen(self, sid, profile=None) -> dict:
        """`profile`: None / "normal" (not in a game) or "ingame"."""
        with self._lock:
            if profile not in PROFILES:
                return _fail("profile must be 'normal' or 'ingame'")
            if (err := self._usable(sid)):
                return _fail(err)
            return self._save_engine_settings(lambda d: _profile_of(d, profile).update(pinned=sid))

    def unpin_screen(self, profile=None) -> dict:
        with self._lock:
            if profile not in PROFILES:
                return _fail("profile must be 'normal' or 'ingame'")
            return self._save_engine_settings(lambda d: _profile_of(d, profile).update(pinned=None))

    def set_rotation(self, ids, interval_s=None, profile=None) -> dict:
        with self._lock:
            if profile not in PROFILES:
                return _fail("profile must be 'normal' or 'ingame'")
            if not isinstance(ids, list) or not ids:
                return _fail("rotation must be a non-empty list of screen ids")
            for sid in ids:
                if (err := self._usable(sid)):
                    return _fail(err)

            def mutate(d):
                p = _profile_of(d, profile)
                p["rotation"] = ids
                if interval_s is not None:
                    p["rotation_interval_s"] = interval_s

            return self._save_engine_settings(mutate)

    def set_screen_enabled(self, sid, enabled) -> dict:
        with self._lock:
            self._need()
            if not isinstance(sid, str) or not isinstance(enabled, bool):
                return _fail("need a screen id and true/false")
            try:
                self.store.enable(sid) if enabled else self.store.disable(sid)
            except PackageError as e:
                return _fail(e.errors)
            if not enabled and sid in (p["pinned"] for p in _profiles(self._need().settings.to_dict())):
                def unpin(d):
                    for p in _profiles(d):
                        if p["pinned"] == sid:
                            p["pinned"] = None

                self._save_engine_settings(unpin)
            self._sync_screens()
            return {"ok": True}

    def set_now_playing(self, appid) -> dict:
        """From the frontend's SteamClient events: appid when a game starts, None when it exits."""
        try:
            self._need().set_now_playing(parse_appid(appid))   # validated here: the frontend is a trust boundary
        except ValueError as e:
            return _fail(str(e))
        return {"ok": True}

    # -- packages -----------------------------------------------------------------
    def import_package(self, source) -> dict:
        with self._lock:
            self._need()
            if not self.host.developer_mode:
                return _fail(DEV_OFF_MSG)
            if not isinstance(source, str) or not source.strip() or len(source) > 2048:
                return _fail("source must be a file path or an https URL")
            try:
                info = self.store.install(source.strip())
            except PackageError as e:
                return _fail(e.errors)
            except OSError as e:
                return _fail(f"{source}: {e.strerror or e}")
            self._sync_screens()
            return {"ok": True, "package": self._info(info), "needs_approval": info.needs_approval}

    def approve_package(self, sid, version, sha256) -> dict:
        with self._lock:
            self._need()
            if not all(isinstance(x, str) for x in (sid, version, sha256)):
                return _fail("id, version and sha256 must be strings")
            try:
                info = self.store.approve(sid, version, sha256)
            except PackageError as e:
                return _fail(e.errors)
            self._sync_screens()
            return {"ok": True, "package": self._info(info)}

    def uninstall_package(self, sid) -> dict:
        with self._lock:
            self._need()
            if not isinstance(sid, str):
                return _fail("screen id must be a string")
            try:
                self.store.uninstall(sid)
            except PackageError as e:
                return _fail(e.errors)

            def mutate(d):
                d["screen_settings"].pop(sid, None)
                for p in _profiles(d):
                    p["rotation"] = [i for i in p["rotation"] if i != sid]
                    if p["pinned"] == sid:
                        p["pinned"] = None

            self._save_engine_settings(mutate)
            if sid in self.host.idle_screens:
                self.host.idle_screens = [i for i in self.host.idle_screens if i != sid]
                self.host.idle_modes.pop(sid, None)
                self.host.save(self.dir / HOST_FILE)
            self._sync_screens()
            return {"ok": True}

    # -- catalog (docs/screen-catalog.md) ----------------------------------------
    def _compat(self, entry) -> tuple[bool, str | None]:
        """(compatible, reason). min_plugin_version > plugin version is incompatible; an unknown plugin version skips the check.
        is_newer(min, cur) is True exactly when cur < min, i.e. the plugin is too old."""
        if self.version != "unknown" and is_newer(entry.min_plugin_version, self.version):
            return False, f"needs plugin {entry.min_plugin_version} or newer; this plugin is {self.version}"
        return True, None

    def _installed_index(self) -> dict:
        """id -> PackageInfo for non-builtin installed screens (the only ones the catalog can match)."""
        return {i.id: i for i in self.store.list() if not i.builtin}

    def _catalog_view(self, entry, installed: dict) -> dict:
        info = installed.get(entry.id)
        compatible, reason = self._compat(entry)
        inst = info is not None
        upd = inst and is_newer(entry.version, info.version)
        return {"id": entry.id, "name": entry.name, "version": entry.version, "author": entry.author,
                "description": entry.description, "has_code": entry.has_code, "has_active": entry.has_active,
                "has_idle": entry.has_idle, "size": entry.size, "min_plugin_version": entry.min_plugin_version,
                "installed": inst, "installed_version": info.version if inst else None, "update_available": upd,
                "needs_approval": bool(inst and info.needs_approval), "compatible": compatible,
                "incompatible_reason": reason}

    def catalog_refresh(self) -> dict:
        with self._lock:
            self._need()
            try:
                idx, fetched_at = self.catalog.refresh()
            except PackageError as e:   # network down: cache stays usable
                return _fail(self._scrub(e.errors))
            except CatalogError as e:
                return _fail(self._scrub(str(e)))
            except OSError as e:   # cache dir not writable, disk full
                return _fail(self._scrub(f"catalog cache: {e.strerror or e}"))
            return {"ok": True, "fetched_at": fetched_at, "count": len(idx.entries)}

    def catalog_list(self) -> dict:
        with self._lock:
            self._need()
            try:
                idx, fetched_at = self.catalog.cached()
            except CatalogError as e:
                return _fail(self._scrub(str(e)))
            except OSError as e:   # cache dir not writable, disk full
                return _fail(self._scrub(f"catalog cache: {e.strerror or e}"))
            installed = self._installed_index()
            entries = [self._catalog_view(e, installed) for e in idx.entries] if idx else []
            return {"ok": True, "entries": entries, "fetched_at": fetched_at, "url": self.catalog.url}

    def catalog_updates(self) -> dict:
        with self._lock:
            self._need()
            try:
                idx, _ = self.catalog.cached()
            except CatalogError as e:
                return _fail(self._scrub(str(e)))
            except OSError as e:   # cache dir not writable, disk full
                return _fail(self._scrub(f"catalog cache: {e.strerror or e}"))
            installed = self._installed_index()
            updates = []
            for e in (idx.entries if idx else ()):
                info = installed.get(e.id)
                if info is not None and is_newer(e.version, info.version):
                    updates.append({"id": e.id, "name": e.name, "installed_version": info.version, "version": e.version})
            return {"ok": True, "updates": updates}

    def catalog_install(self, screen_id, version) -> dict:
        with self._lock:
            self._need()
            if not isinstance(screen_id, str) or not isinstance(version, str):
                return _fail("screen id and version must be strings")
            try:
                idx, _ = self.catalog.cached()
                if idx is None:
                    return _fail("no catalog is saved yet; refresh the catalog first")
                entry = self.catalog.entry(idx, screen_id)
                if version != entry.version:
                    return _fail(f"the catalog lists {screen_id} {entry.version}, not {version}; refresh the catalog")
                compatible, reason = self._compat(entry)
                if not compatible:
                    return _fail(reason)
                data = self.catalog.fetch_zip(entry)
            except PackageError as e:
                return _fail(self._scrub(e.errors))
            except CatalogError as e:
                return _fail(self._scrub(str(e)))
            except OSError as e:   # cache dir not writable, disk full
                return _fail(self._scrub(f"catalog cache: {e.strerror or e}"))
            try:
                info = self.store.install_bytes(data, expect=(entry.id, entry.version, entry.has_code))
            except PackageError as e:
                return _fail(e.errors)
            except OSError as e:
                return _fail(f"{screen_id}: {e.strerror or e}")
            self._sync_screens()
            return {"ok": True, "package": self._info(info), "needs_approval": info.needs_approval}

    def catalog_preview(self, screen_id) -> dict:
        with self._lock:
            self._need()
            if not isinstance(screen_id, str):
                return _fail("screen id must be a string")
            try:
                idx, _ = self.catalog.cached()
                if idx is None:
                    return _fail("no catalog is saved yet; refresh the catalog first")
                png = self.catalog.preview_png(self.catalog.entry(idx, screen_id))
            except PackageError as e:
                return _fail(self._scrub(e.errors))
            except CatalogError as e:
                return _fail(self._scrub(str(e)))
            except OSError as e:   # cache dir not writable, disk full
                return _fail(self._scrub(f"catalog cache: {e.strerror or e}"))
            return {"ok": True, "data_url": "data:image/png;base64," + base64.b64encode(png).decode()}

    # -- firmware (docs/ota.md) -----------------------------------------------------
    def _fw(self) -> FirmwareManager:
        self._need()
        return self.firmware

    def get_firmware_status(self) -> dict:
        return self._fw().status()

    def check_firmware(self) -> dict:
        """Look for a release now (the daily check runs by itself). Returns the status; a failure is in status['check']."""
        return self._fw().check()

    def install_firmware(self, version) -> dict:
        """Start the update in the background; the user confirmed in the UI. Progress: `firmware` events / get_firmware_status."""
        return self._fw().install(version)

    def cancel_firmware_install(self) -> dict:
        return self._fw().cancel()

    # -- preview / idle -----------------------------------------------------------
    def preview_screen(self, sid) -> dict:
        """Render a screen with the live data and the user's settings, no panel needed: a PNG data URL."""
        eng = self._need()
        try:
            info = self._pkg(sid)
            snap, over = eng.registry.snapshot(), eng.settings.screen_settings.get(sid)
            if info.has_code:   # never runs unapproved code: the store checks
                img = self.store.render(sid, snap, over)
            else:               # declarative: safe to show even while disabled
                img = render_declarative(load_screen(info.path, sid), snap, over)
        except ScreenError as e:
            return _fail(e.errors)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return {"ok": True, "data_url": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()}

    def preview_idle_screen(self, sid, mode=None) -> dict:
        """What the panel's own idle renderer draws for this screen: a 648x480 PNG data URL (1-bit for 'mono', 4 grays for
        'gray4'). The clock is live; weather, Steam and other remote values come from the package's sample-data.json (or draw
        as placeholders). Runs only the declarative idle layout, never a package's screen.py, and never an unapproved package.
        `mode` None = the user's choice (idle_modes), else what the layout declares. Never raises."""
        self._need()
        try:
            info = self._pkg(sid)
            if info.needs_approval:
                return _fail(f"{sid}: needs approval before it can be previewed")
            scr = load_screen(info.path, sid)
            if scr.idle is None:
                return _fail(f"{sid}: has no idle layout")
            if mode is not None and (not isinstance(mode, str) or mode not in MODES):
                return _fail("mode must be 'mono' or 'gray4'")
            ok = supported_modes(scr.idle, scr.assets_dir)
            if mode is None:   # the user's choice, else the declared mode, else whatever compiles
                mode = next((m for m in (self.host.idle_modes.get(sid), scr.idle.get("render_mode"), *ok) if m in ok), None)
                if mode is None:
                    return _fail(f"{sid}: the idle layout does not compile")
            elif mode not in ok:
                return _fail(f"{sid}: cannot be shown in {mode} mode")
            png = self._idle_renderer.render(IDLE_RENDER_BIN, scr.idle, scr.assets_dir, mode, load_sample(info.path),
                                             tz=posix_tz())
        except ScreenError as e:   # PackageError is a ScreenError
            return _fail(self._scrub(e.errors))
        except PreviewError as e:
            return _fail(self._scrub(str(e)))
        except Exception:  # noqa: BLE001 - a callable must not raise for any input
            log.exception("preview_idle_screen failed")   # no values: the stack only
            return _fail("idle preview failed")
        return {"ok": True, "data_url": "data:image/png;base64," + base64.b64encode(png).decode()}

    def push_idle_layouts(self) -> dict:
        """Compile the selected idle screens and send them to the panel now (it also happens by itself on connect and
        on every change). `ok` means the upload started; progress and the outcome are in get_status()["idle"]."""
        eng = self._need()
        st = eng.status()
        if not st["connected"]:
            return {**_fail("panel not connected"), "supported": False}
        if not (st["caps"] or 0) & P.CAP_IDLE_STORAGE:
            return {**_fail("this firmware has no idle storage: update the firmware"), "supported": False}
        plan = self._idle_plan()
        if plan.error:
            return {**_fail(self._scrub(plan.error)), "supported": True}
        if not plan.ids:
            return {**_fail("no idle screens selected"), "supported": True}
        eng.set_idle_plan(plan, force=True)
        return {"ok": True, "supported": True, "idle": eng.status()["idle"]}
