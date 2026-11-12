"""Where screens live: read-only built-ins plus installed community packages, with enable/approve state.

    <root>/state.json                    {"schema": 1, "packages": {"<id>": {"version", "enabled", "approval"}}}
    <root>/<id>/<version>/screen.json    one installed package (only the active version is kept)
    <root>/.tmp/                         scratch for downloads/extraction (same filesystem => atomic rename)

In Decky `root` is `DECKY_PLUGIN_SETTINGS_DIR/screens` and `builtin_dir` is the plugin's `screens/`.
The state file is the commit point: a package exists once `state.json` names it.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from ..screens import ScreenError, load_screen
from ..screens import render as render_declarative
from ..screens.data import DataSnapshot
from ..screens.schema import ID_RE, SEMVER_RE, Screen
from .extract import MAX_ZIP_BYTES, PackageError, load_zip
from .fetch import DEFAULT_TIMEOUT_S, Fetch, urllib_fetch
from .sandbox import TIMEOUT_S, error_frame, run_code

log = logging.getLogger(__name__)
STATE_FILE = "state.json"
_URL = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://")
_SEMVER_PARTS = re.compile(r"(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?")


@dataclass(frozen=True)
class PackageInfo:
    id: str
    version: str
    name: str
    author: str
    description: str
    builtin: bool
    enabled: bool
    has_code: bool
    code_sha256: str | None   # sha256 of screen.py as it is on disk now
    approved: bool            # the user approved exactly this version and this sha256 (always True for built-ins)
    path: Path

    @property
    def needs_approval(self) -> bool:
        return self.has_code and not self.approved


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _version_key(v: str):
    """Semver precedence (build metadata ignored): 1.0.0-rc.1 < 1.0.0 < 1.10.0."""
    major, minor, patch, pre = _SEMVER_PARTS.match(v).groups()
    ids = tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in pre.split(".")) if pre else ()
    return int(major), int(minor), int(patch), pre is None, ids


def _entry_ok(pid, e) -> bool:
    ap = e.get("approval") if isinstance(e, dict) else None
    return (isinstance(pid, str) and bool(ID_RE.fullmatch(pid)) and isinstance(e, dict)
            and isinstance(e.get("enabled"), bool)
            and (e.get("version") is None or (isinstance(e["version"], str) and bool(SEMVER_RE.fullmatch(e["version"]))))
            and (ap is None or (isinstance(ap, dict) and isinstance(ap.get("version"), str) and isinstance(ap.get("sha256"), str))))


class PackageStore:
    def __init__(self, root: str | Path, builtin_dir: str | Path | None = None, *, fetch: Fetch = urllib_fetch,
                 fetch_timeout: float = DEFAULT_TIMEOUT_S, code_timeout: float = TIMEOUT_S, python: str | None = None):
        self.root = Path(root)
        self.builtin_dir = Path(builtin_dir) if builtin_dir else None
        self._fetch, self._fetch_timeout, self._code_timeout, self._python = fetch, fetch_timeout, code_timeout, python
        self._lock = threading.RLock()   # ponytail: one lock for the whole store; fine for a handful of packages
        self.root.mkdir(parents=True, exist_ok=True)
        self._tmp = self.root / ".tmp"
        shutil.rmtree(self._tmp, ignore_errors=True)   # leftovers of a crashed install
        self._tmp.mkdir()
        self._entries = self._load_state()

    # -- state -------------------------------------------------------------------------------------------
    def _load_state(self) -> dict[str, dict]:
        f = self.root / STATE_FILE
        try:
            raw = json.loads(f.read_text("utf-8"))
            entries = raw["packages"]
            if not isinstance(entries, dict):
                raise ValueError("'packages' is not an object")
        except FileNotFoundError:
            return {}
        except (OSError, ValueError, KeyError, TypeError) as e:
            # Fail closed: approvals are forgotten rather than guessed.
            log.error("%s unreadable (%s); starting empty, old file kept as %s.bad", f, e, STATE_FILE)
            f.replace(f.with_name(STATE_FILE + ".bad"))
            return {}
        good = {k: v for k, v in entries.items() if _entry_ok(k, v)}
        if len(good) != len(entries):
            log.error("%s: dropped %d malformed entries", f, len(entries) - len(good))
        return good

    def _save_state(self) -> None:
        f = self.root / STATE_FILE
        tmp = f.with_name(STATE_FILE + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"schema": 1, "packages": self._entries}, fh, indent=1, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, f)

    def _commit(self, new: dict[str, dict]) -> None:
        """Swap in `new` entries and persist; on failure the in-memory state is restored."""
        old = self._entries
        self._entries = new
        try:
            self._save_state()
        except BaseException:
            self._entries = old
            raise

    # -- reading -----------------------------------------------------------------------------------------
    def _builtin_ids(self) -> list[str]:
        d = self.builtin_dir
        return sorted(p.name for p in d.iterdir() if (p / "screen.json").is_file()) if d and d.is_dir() else []

    def _installed_dir(self, pid: str, version: str) -> Path:
        return self.root / pid / version

    def _load(self, pid: str) -> tuple[PackageInfo, Screen, bytes | None]:
        """Load one package and re-check its approval against the code bytes read NOW (those bytes are what runs)."""
        with self._lock:
            builtin = pid in self._builtin_ids()
            entry = self._entries.get(pid)
            if builtin:
                path, enabled = self.builtin_dir / pid, (entry or {}).get("enabled", True)
            elif entry and entry.get("version"):
                path, enabled = self._installed_dir(pid, entry["version"]), entry["enabled"]
            else:
                raise PackageError(f"{pid}: not installed")
            try:
                screen = load_screen(path, pid)
            except ScreenError as e:
                raise PackageError(e.errors) from None
            code = screen.code_path.read_bytes() if screen.code_path else None
            sha = sha256_hex(code) if code is not None else None
            approved = True
            if code is not None and not builtin:
                ap = entry.get("approval")
                approved = bool(ap) and ap == {"version": entry["version"], "sha256": sha}
                if ap and not approved:
                    log.warning("%s: screen.py changed since approval; approval revoked", pid)
                    self._commit({**self._entries, pid: {**entry, "approval": None, "enabled": False}})
                    enabled = False
                elif not approved:
                    enabled = False   # also covers a hand-edited state file
            info = PackageInfo(pid, screen.version, screen.name, screen.author, screen.description, builtin,
                               enabled and approved, code is not None, sha, approved, path)
            return info, screen, code

    def get(self, pid: str) -> PackageInfo:
        return self._load(pid)[0]

    def list(self) -> list[PackageInfo]:
        """Built-ins and installed packages, sorted by id. Packages that fail to load are skipped (and logged)."""
        with self._lock:
            ids = sorted(set(self._builtin_ids()) | {k for k, v in self._entries.items() if v.get("version")})
            out = []
            for pid in ids:
                try:
                    out.append(self.get(pid))
                except PackageError as e:
                    log.warning("skipping %s: %s", pid, "; ".join(e.errors))
            return out

    # -- enable / approve --------------------------------------------------------------------------------
    def _set_enabled(self, pid: str, enabled: bool) -> PackageInfo:
        with self._lock:
            info = self.get(pid)
            if enabled and info.needs_approval:
                raise PackageError(f"{pid}: runs code and needs approval first (approve {info.version} "
                                   f"sha256 {info.code_sha256})")
            self._commit({**self._entries, pid: {**self._entries.get(pid, {}), "enabled": enabled}})
            return self.get(pid)

    def enable(self, pid: str) -> PackageInfo:
        return self._set_enabled(pid, True)

    def disable(self, pid: str) -> PackageInfo:
        return self._set_enabled(pid, False)

    def approve(self, pid: str, version: str, sha256: str) -> PackageInfo:
        """The user saw author/id/version/sha256 of screen.py and agreed. Enables the package."""
        with self._lock:
            info = self.get(pid)
            if info.builtin or not info.has_code:
                raise PackageError(f"{pid}: has no installed screen.py to approve")
            if version != info.version:
                raise PackageError(f"{pid}: installed version is {info.version}, not {version}")
            if sha256.strip().lower() != info.code_sha256:
                raise PackageError(f"{pid}: screen.py changed since it was shown to you (sha256 mismatch); review it again")
            e = self._entries[pid]
            self._commit({**self._entries, pid: {**e, "approval": {"version": version, "sha256": info.code_sha256},
                                                  "enabled": True}})
            return self.get(pid)

    # -- install / uninstall -----------------------------------------------------------------------------
    def _read_source(self, source: str | Path) -> Path | bytes:
        if isinstance(source, str) and _URL.match(source):
            if not source.lower().startswith("https://"):
                raise PackageError(f"{source}: only https:// URLs are accepted")
            return self._fetch(source, MAX_ZIP_BYTES, self._fetch_timeout)
        return Path(source)

    def install(self, source: str | Path) -> PackageInfo:
        """Install or upgrade from a local zip path or an https URL. Nothing changes unless the whole package is valid."""
        return self._install(self._read_source(source))

    def install_bytes(self, data: bytes, expect: tuple[str, str, bool] | None = None) -> PackageInfo:
        """Install from zip bytes already in memory (the catalog downloaded them). `expect` = (id, version, has_code):
        checked after load_zip and BEFORE the commit, so a zip that does not match the signed catalog entry is refused
        before anything touches disk state."""
        return self._install(data, expect)

    def _install(self, src: str | Path | bytes, expect: tuple[str, str, bool] | None = None) -> PackageInfo:
        with self._lock, tempfile.TemporaryDirectory(dir=self._tmp) as td:
            screen = load_zip(src, Path(td))
            pid, version = screen.id, screen.version
            has_code = screen.code_path is not None
            if expect is not None:
                want_id, want_version, want_code = expect
                if (pid, version, has_code) != (want_id, want_version, want_code):
                    raise PackageError(f"package does not match the catalog: it is {pid} {version} "
                                       f"(code={has_code}), the catalog lists {want_id} {want_version} (code={want_code})")
            if pid in self._builtin_ids():
                raise PackageError(f"id: '{pid}' collides with a built-in screen")
            prev = self._entries.get(pid)
            if prev and prev.get("version"):
                pv = prev["version"]
                if _version_key(version) <= _version_key(pv):
                    raise PackageError(f"version: {version} is not newer than the installed {pv} "
                                       f"(uninstall {pid} first to go back)")
            entry = {"version": version, "approval": None,
                     "enabled": (prev["enabled"] if prev else True) and not has_code}
            dest = self._installed_dir(pid, version)
            shutil.rmtree(dest, ignore_errors=True)   # an orphan from a crashed install
            dest.parent.mkdir(parents=True, exist_ok=True)
            screen.root.rename(dest)
            try:
                self._commit({**self._entries, pid: entry})
            except BaseException:
                shutil.rmtree(dest, ignore_errors=True)
                raise
            if prev and prev.get("version"):
                shutil.rmtree(self._installed_dir(pid, prev["version"]), ignore_errors=True)
        return self.get(pid)

    def uninstall(self, pid: str) -> None:
        with self._lock:
            if pid in self._builtin_ids():
                raise PackageError(f"{pid}: built-in screens cannot be uninstalled (disable it instead)")
            if not (self._entries.get(pid) or {}).get("version"):
                raise PackageError(f"{pid}: not installed")
            self._commit({k: v for k, v in self._entries.items() if k != pid})
            shutil.rmtree(self.root / pid, ignore_errors=True)

    # -- rendering ---------------------------------------------------------------------------------------
    def render(self, pid: str, data: Mapping | None = None, settings: Mapping | None = None) -> Image.Image:
        """648x480 1-bit frame. Declarative screens render in-process; approved code runs in a child process and a
        failing screen.py yields an error frame, never an exception. Raises PackageError if the screen is disabled."""
        info, screen, code = self._load(pid)
        if not info.enabled:
            raise PackageError(f"{pid}: disabled" if not info.needs_approval else f"{pid}: needs approval")
        if code is None:
            return render_declarative(screen, data, settings)
        resolved = screen.resolve_settings(settings)
        raw = data.raw if isinstance(data, DataSnapshot) else (data or {})
        try:
            return run_code(code, info.path, raw, resolved, timeout=self._code_timeout, python=self._python)
        except PackageError as e:
            msg = "; ".join(e.errors)
            log.warning("%s: %s", pid, msg)
            return error_frame(screen.name, msg)
