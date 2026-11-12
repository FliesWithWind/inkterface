"""Firmware updates for the plugin: daily release check, status for the UI, user-triggered install.

Glue between `ota/` (verify, download, stream) and the rest of the plugin; docs/ota.md has the protocol and
trust model, docs/plugin-ui.md the callables. Rules this module enforces:

- Never installs by itself. `install()` is only called by a user action.
- No signing key bundled (ota/trust.py empty) -> no network, no install, and the status says so.
- The engine lets go of the port (`Engine.pause`) for the whole install and always gets it back.
- Everything shown to the user comes from `OtaError.__str__` (safe) or a fixed text.

Time and network are injected (`clock`, `sleep`, `fetch`) so tests run in milliseconds.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from . import proto as P
from .ota import github, manifest as M, stream, trust
from .ota.errors import BootMismatch, Cancelled, Downgrade, NoTrustKey, OtaError, RateLimited
from .usb_link import Link

log = logging.getLogger(__name__)

CHECK_EVERY_S = 24 * 3600
RETRY_AFTER_ERROR_S = 3600
MIN_RECHECK_S = 60          # a manual check this soon after a successful one reuses its result (no request)
MAX_LIMIT_WAIT_S = 2 * 3600  # GitHub resets within the hour; never trust a reset time further out than this
LIMIT_MSG = ("GitHub allows 60 anonymous requests per hour per network, shared by every device behind your router, "
             "and this network has used them up.")
TICK_S = 30.0  # how often the scheduler thread looks at the clock
NO_KEY_MSG = ("No release signing key is bundled in this plugin build, so firmware updates are disabled "
              "(the maintainer has not published the production key yet).")

IDLE, DOWNLOADING, PREPARING, SENDING, CONFIRMING, DONE, FAILED, CANCELLED = (
    "idle", "downloading", "preparing", "sending", "confirming", "done", "failed", "cancelled")
BUSY = (DOWNLOADING, PREPARING, SENDING, CONFIRMING)


def _fail(msg: str) -> dict:
    return {"ok": False, "error": msg}


class FirmwareManager:
    """engine: needs status(), pause(), resume(). connect(): opens the panel's serial port (raises OSError)."""

    def __init__(self, engine_getter: Callable, connect: Callable, *, plugin_version: str, fetch=github.urllib_fetch,
                 public_key: Callable[[], bytes | None] = trust.public_key, clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep, on_event: Callable[[dict], None] | None = None,
                 handshake_timeout: float = 30.0, confirm_timeout: float = 120.0, tick_s: float = TICK_S,
                 stream_kw: dict | None = None, cache_path=None):
        self._engine, self._connect, self._fetch, self._key = engine_getter, connect, fetch, public_key
        self.plugin_version, self._clock, self._sleep, self._emit = plugin_version, clock, sleep, on_event
        self._hs_timeout, self._confirm_timeout, self._tick_s = handshake_timeout, confirm_timeout, tick_s
        self._stream_kw = stream_kw or {}
        self._cache = github.ReleaseCache(cache_path)   # last listing + ETag; None path = memory only
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None   # scheduler
        self._worker: threading.Thread | None = None   # install
        self._checking = False
        self._release: github.Release | None = None
        self._check = {"at": None, "ok": None, "error": None, "error_kind": None, "retry_at": None, "limit_message": None}
        self._blocked_until = 0.0   # GitHub said no: no request before this time (manual checks included)
        self._next_check_at: float | None = None
        self._install = self._idle_install()
        self._confirmed_fw: str | None = None   # fw_ver the panel confirmed after an install, until the engine sees it too

    # -- scheduler ----------------------------------------------------------------
    def start(self) -> None:
        with self._lock:
            if self._thread is not None:
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="inkterface-fwcheck", daemon=True)
            self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Cancel a running install (the engine gets the port back), stop the scheduler."""
        self._stop.set()
        self._cancel.set()
        with self._lock:
            t, self._thread = self._thread, None
            w = self._worker
        for th in (t, w):
            if th is not None and th is not threading.current_thread():
                th.join(timeout)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - the scheduler must outlive a bug
                log.exception("firmware check failed")
            self._stop.wait(self._tick_s)

    def tick(self) -> bool:
        """Run the release check if it is due (first call: now). True if a check ran."""
        with self._lock:
            due = self._next_check_at is None or self._clock() >= self._next_check_at
            if not due or self._install["state"] in BUSY:
                return False
        self.check(auto=True)
        return True

    # -- check --------------------------------------------------------------------
    def check(self, auto: bool = False) -> dict:
        """Ask GitHub for the newest signed release now. Always returns the status; failure is in status()['check'].

        `auto` (the scheduler): a release list fetched less than 24 h ago, even by an earlier plugin run, is reused with
        no API request, so restarting the plugin (a Decky reload, a reboot) does not eat the 60/h shared quota."""
        with self._lock:
            now = self._clock()
            fresh = self._check["ok"] and now - self._check["at"] < MIN_RECHECK_S
            if self._checking or fresh or now < self._blocked_until:
                return self.status()
            self._checking = True
        self._notify()
        err, kind, rel, retry_at, limited = None, None, None, None, False
        try:
            key = self._key()
            if not key:
                raise NoTrustKey(NO_KEY_MSG)
            # a pre-release plugin (1.0.0-beta.1) follows pre-release firmware too; a stable plugin never offers one
            rel = github.find_release(self._fetch, key, plugin_version=self.plugin_version,
                                      include_prerelease=M.is_prerelease(self.plugin_version), cache=self._cache,
                                      fresh_s=CHECK_EVERY_S if auto else 0.0, now=self._clock())
            e = self._cache.limited   # the API said no but the cache / github.com download path answered
            limited, retry_at = e is not None, e and self._lift_time(e)
        except RateLimited as e:
            err, kind, limited, retry_at = str(e), "RateLimited", True, self._lift_time(e)
        except OtaError as e:
            err, kind = str(e), type(e).__name__
        except ValueError as e:  # a plugin version that is not semver ("unknown")
            err, kind = f"cannot compare versions: {e}", "ValueError"
        except Exception as e:  # noqa: BLE001 - a surprise must not kill the scheduler or the UI
            log.exception("firmware check")
            err, kind = f"unexpected error: {type(e).__name__}: {e}", type(e).__name__
        now = self._clock()
        if retry_at is not None:
            retry_at = min(retry_at, now + MAX_LIMIT_WAIT_S)
        with self._lock:
            self._checking = False
            self._check = {"at": now, "ok": err is None, "error": err, "error_kind": kind,
                           "retry_at": retry_at if limited else None,
                           "limit_message": (LIMIT_MSG + ("" if err else " Showing the last known release.")) if limited else None}
            if limited:   # no API request before the reset (an unknown reset: an hour)
                self._blocked_until = retry_at if retry_at is not None else now + RETRY_AFTER_ERROR_S
            if err is None:
                at = self._cache.checked_at   # when the list was really fetched: a reused one keeps its schedule
                self._release = rel
                self._next_check_at = max(now + 1, at + CHECK_EVERY_S) if at and not limited else now + CHECK_EVERY_S
            else:   # keep the last good release: a GitHub outage must not hide a known update
                wait = RETRY_AFTER_ERROR_S if kind != "RateLimited" or retry_at is None else max(retry_at - now, RETRY_AFTER_ERROR_S)
                self._next_check_at = now + wait
                if kind == "NoTrustKey":
                    self._next_check_at = now + CHECK_EVERY_S
        self._notify()
        return self.status()

    def _lift_time(self, e: RateLimited) -> float | None:
        """Unix time (on our clock) GitHub lets us back in. Retry-After is relative, so it is immune to clock skew."""
        return self._clock() + e.retry_after if e.retry_after is not None else e.retry_at

    # -- status -------------------------------------------------------------------
    @staticmethod
    def _idle_install() -> dict:
        return {"state": IDLE, "version": None, "done": 0, "total": 0, "percent": 0, "message": None, "error": None,
                "cancellable": False, "rollback_pending": False}

    def _installed(self) -> tuple[str | None, dict]:
        st = self._engine().status()
        fw = st["fw_version"]
        if self._confirmed_fw is not None:
            if st["connected"] and fw == self._confirmed_fw:
                self._confirmed_fw = None
            else:
                fw = self._confirmed_fw
        return fw, st

    def status(self) -> dict:
        with self._lock:
            fw, st = self._installed()
            key = self._key()
            sv = M.installed_version(fw) if fw else None
            rel, update, note = self._release, False, None
            if rel is not None:
                try:
                    update = M.check_policy(rel.manifest, plugin_version=self.plugin_version, installed=sv)
                except Downgrade:
                    note = f"The panel runs {fw}, which is newer than the latest release {rel.manifest.version}."
                except (OtaError, ValueError) as e:
                    note = str(e)
            proto = st["protocol_version"]
            mismatch = proto is not None and proto != P.PROTO_VER
            return {
                "installed": {"version": fw, "dev_build": bool(fw) and sv is None, "protocol_version": proto,
                              "connected": bool(st["connected"]), "seen": fw is not None},
                "latest": None if rel is None else {
                    "version": rel.manifest.version, "tag": rel.tag, "changelog": rel.manifest.changelog,
                    "size": rel.manifest.size, "page_url": rel.page_url},
                "update_available": bool(update),
                "note": note,
                "trust": {"state": "ok" if key else "no_key", "message": None if key else NO_KEY_MSG},
                "check": {**self._check, "next_at": self._next_check_at, "checking": self._checking},
                "protocol_mismatch": mismatch,
                "mismatch_message": (f"The panel firmware speaks protocol v{proto}; this plugin needs v{P.PROTO_VER}. "
                                     "Install the latest firmware below to fix it.") if mismatch else None,
                "install": dict(self._install),
            }

    def _notify(self) -> None:
        if self._emit is not None:
            try:
                self._emit(self.status())
            except Exception:  # noqa: BLE001 - UI plumbing must never break an install
                log.exception("firmware event")

    def _set_install(self, **kw) -> None:
        with self._lock:
            self._install.update(kw)
        self._notify()

    # -- install ------------------------------------------------------------------
    def install(self, version) -> dict:
        """Start installing the verified release `version` in the background. Progress arrives as events/status()."""
        with self._lock:
            if self._install["state"] in BUSY:
                return _fail("a firmware update is already running")
            if not self._key():
                return _fail(NO_KEY_MSG)
            rel = self._release
            if rel is None:
                return _fail("no verified firmware release is known; check for updates first")
            if version != rel.manifest.version:
                return _fail(f"firmware {version!r} is not the latest verified release ({rel.manifest.version}); check for updates again")
            fw, _ = self._installed()
            if fw is None:
                return _fail("the panel has not been seen yet; plug in the Inkterface USB cable first")
            try:
                if not M.check_policy(rel.manifest, plugin_version=self.plugin_version, installed=M.installed_version(fw)):
                    return _fail(f"the panel already runs {rel.manifest.version}")
            except (OtaError, ValueError) as e:
                return _fail(str(e))
            self._cancel.clear()
            self._install = {**self._idle_install(), "state": DOWNLOADING, "version": version, "total": rel.manifest.size,
                             "cancellable": True, "message": f"Downloading firmware {version}…"}
            self._worker = threading.Thread(target=self._run_install, args=(rel,), name="inkterface-fwinstall", daemon=True)
        self._notify()   # before the worker runs: otherwise a fast worker can reach "preparing" first and "downloading" is never emitted
        self._worker.start()
        return {"ok": True}

    def cancel(self) -> dict:
        """Stop an update that has not finished sending. Once the image is written the panel decides (confirm or roll back)."""
        with self._lock:
            if self._install["state"] not in BUSY:
                return _fail("no firmware update is running")
            if not self._install["cancellable"]:
                return _fail("too late to cancel: the image is already on the panel, which is rebooting into it")
            self._cancel.set()
        return {"ok": True}

    def _open_panel(self):
        try:
            ser = self._connect()
        except OSError as e:
            raise OSError(f"cannot open the panel: {e}") from None
        link = Link(ser)
        try:
            return link, stream.handshake(link, timeout=self._hs_timeout)
        except BaseException:
            try:
                ser.close()
            except Exception:  # noqa: BLE001
                pass
            raise

    def _progress(self, done: int, total: int) -> None:
        pct = done * 100 // total if total else 0
        with self._lock:
            cur = self._install
            if cur["state"] not in (SENDING, PREPARING) or (cur["state"] == SENDING and pct == cur["percent"] and done < total):
                return
            full = done >= total
            self._install.update(state=CONFIRMING if full else SENDING, done=done, total=total, percent=pct, cancellable=not full,
                                 message="Waiting for the panel to restart and confirm the new firmware…" if full
                                 else f"Sending firmware… {pct}%")
        self._notify()

    def _run_install(self, rel: github.Release) -> None:
        v = rel.manifest.version
        paused = False
        try:
            image = github.download_image(self._fetch, rel)
            if self._cancel.is_set():
                raise Cancelled("update cancelled")
            self._set_install(state=PREPARING, message="Releasing the panel…")
            self._engine().pause()
            paused = True
            hello = stream.install(self._open_panel, image, v, progress=self._progress, cancel=self._cancel.is_set,
                                   sleep=self._sleep, confirm_timeout=self._confirm_timeout, **self._stream_kw)
            with self._lock:
                self._confirmed_fw = hello.fw_ver
            self._set_install(state=DONE, done=len(image), total=len(image), percent=100, cancellable=False,
                              message=f"Firmware {v} is installed and confirmed.", error=None)
        except Cancelled:
            self._set_install(state=CANCELLED, cancellable=False, error=None,
                              message="Update cancelled. The panel keeps its current firmware.")
        except OtaError as e:
            rb = isinstance(e, BootMismatch)
            self._fail_install(str(e), rollback_pending=rb)
        except OSError as e:
            self._fail_install(str(e) or type(e).__name__)
        except Exception as e:  # noqa: BLE001 - never leave the UI stuck on "sending"
            log.exception("firmware install")
            self._fail_install(f"unexpected error: {type(e).__name__}: {e}")
        finally:
            if paused:
                try:
                    self._engine().resume()
                except RuntimeError:   # plugin is shutting down: the engine is gone, nothing to give back
                    pass

    def _fail_install(self, err: str, rollback_pending: bool = False) -> None:
        log.warning("firmware install failed: %s", err)
        msg = "Update failed. The panel keeps (or returns to) its previous firmware."
        if rollback_pending:
            msg = ("The panel did not confirm the new firmware. It rolls back to the previous version by itself "
                   "within about 3 minutes; nothing is bricked.")
        self._set_install(state=FAILED, cancellable=False, error=err, message=msg, rollback_pending=rollback_pending)
