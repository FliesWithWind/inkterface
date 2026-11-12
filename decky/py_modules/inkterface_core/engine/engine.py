"""Host runtime: keeps the panel showing the current active screen. See docs/engine.md.

One non-blocking `step()` does everything (read replies, timers, render, diff, send). `run()`
calls it on a short sleep; tests call it with a fake clock. No Decky imports, stdlib + Pillow.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field

from .. import diff as D
from .. import proto as P
from .. import usb_link
from ..idle import fields as IF
from ..idle.plan import IdlePlan
from ..screens import ScreenError, load_screen, render_frame
from .scheduler import Scheduler
from .settings import Settings

log = logging.getLogger(__name__)

BACKOFF_MIN_S, BACKOFF_MAX_S = 2.0, 60.0
HELLO_RETRY_S = 2.0
HELLO_TIMEOUT_S = 30.0  # a freshly reset panel answers ~25 s late (Wi-Fi connect in setup())
ACK_TIMEOUT_S = 5.0
IDLE_ACK_TIMEOUT_S = 8.0  # the last IDLE_LAYOUT chunk is acknowledged after a flash erase + write + read-back
REFRESH_TIMEOUT_S = 15.0  # full refresh is ~4 s; the panel's own host timeout is 15 s too
REFUSED_RETRY_S = 60.0
MAX_CONSECUTIVE_NAKS = 3
SLEEP_ACK_S = 3.0        # prepare_sleep(): wait this long for the panel's ACK of MODE PANEL (logind lets a suspend wait 5 s)
SHUTDOWN_ACK_S = 0.5     # stop(): same wait, so the last frame is not lost to the port closing
WAKE_FAILSAFE_S = 20.0   # asleep this long on the engine's own clock (frozen during a suspend) = the suspend never happened
POLL_S = 0.02
GHOST_MIN_PARTIALS = 30   # a ghost-clear needs at least this many partials since the last full refresh: nothing to clear before
GHOST_DEFER_LIMIT = 1.5   # a due ghost-clear full refresh waits for a quiet moment, but at most until ghost_clear_minutes * this
GHOST_QUIET_S = 2.0     # quiet = nothing was sent for this long and the render has nothing to send
MIN_RENDER_GAP_S = 0.1  # floor between two renders, so a stream of change events cannot spin the renderer
_UNSET = object()


def ghost_phase(minutes: int, partials: int, since_full_s: float) -> str:
    """Where the ghost-clear stands: "off" (minutes == 0), "wait" (a gate is closed), "due" (run it at a quiet render) or
    "forced" (run it now, quiet or not). Both gates must be open: `minutes` since the last full refresh AND GHOST_MIN_PARTIALS
    partials. Forced at minutes * GHOST_DEFER_LIMIT, so a screen that never rests still gets cleared."""
    if not minutes:
        return "off"
    limit = minutes * 60.0
    if partials < GHOST_MIN_PARTIALS or since_full_s < limit:
        return "wait"
    return "forced" if since_full_s >= limit * GHOST_DEFER_LIMIT else "due"
IDLE_CHUNK = 4000  # IDLE_LAYOUT data bytes per frame (panel accepts up to 4088); two fit the 8 KiB rx window

DISCONNECTED, HANDSHAKE, READY, REFUSED, STOPPED, PAUSED = "disconnected", "handshake", "ready", "refused", "stopped", "paused"
_INVERT = bytes(b ^ 0xFF for b in range(256))  # screens pack 1 = white, the wire is 1 = black
_FULL_BANDS = D.split_rect((0, 0, P.WIDTH, P.HEIGHT))


def usb_connect(serial: str | None = None, find=usb_link.find_panels, open_port=usb_link.open_port):
    """Open the panel by USB VID 303a (never the first ttyACM). DTR/RTS stay low (see usb_link.open_port)."""
    paths = find(serial)
    if not paths:
        raise FileNotFoundError("no panel found (USB VID 303a)")
    try:
        return open_port(paths[0])
    except PermissionError as e:  # Decky drops to `deck` without its groups: only a udev uaccess ACL helps (docs/using.md)
        raise PermissionError(e.errno, f"{e.strerror}: {paths[0]}. The plugin runs as user `deck`, which needs access to the panel: "
                                       "see 'Serial permission' in the readme") from e


def load_screens(*dirs) -> tuple[dict, list[str]]:
    """{id: Screen} for every valid package with an `active` section inside `dirs`, plus error messages."""
    import pathlib

    out, errs = {}, []
    for d in dirs:
        p = pathlib.Path(d)
        for pkg in sorted(x for x in p.iterdir() if (x / "screen.json").is_file()) if p.is_dir() else []:
            try:
                s = load_screen(pkg)
            except ScreenError as e:
                errs.append(f"{pkg.name}: {e.errors[0]}" + (f" (+{len(e.errors) - 1} more)" if len(e.errors) > 1 else ""))
                continue
            if s.active is not None:
                out[s.id] = s  # later dirs override earlier ones
    return out, errs


@dataclass
class Status:
    state: str = DISCONNECTED
    fw_version: str | None = None
    protocol_version: int | None = None
    panel_size: tuple | None = None
    caps: int | None = None  # HELLO capability bits (proto.CAP_*)
    idle_flash_free: int | None = None  # HELLO: bytes free for idle layouts
    current_screen: str | None = None
    now_playing: int | None = None
    last_error: str | None = None
    last_error_at: float | None = None  # wall clock
    last_frame_at: float | None = None  # wall clock of the last finished refresh
    retry_in_s: float | None = None
    frames_rendered: int = 0
    unchanged_ticks: int = 0  # renders identical to what the panel shows: nothing sent
    coalesced: int = 0  # renders that folded in changes that arrived during a refresh
    ghost_deferred: int = 0  # renders that sent a partial although the ghost-clear full refresh was due (changes were pending)
    rects_sent: int = 0
    bytes_sent: int = 0
    heartbeats_sent: int = 0
    partial_refreshes: int = 0
    full_refreshes: int = 0
    naks: int = 0
    reconnects: int = 0
    asleep: bool = False  # the host is suspending: the panel was told to show its idle screens, no frames until wake()
    settings_error: str | None = None
    screen_errors: list = field(default_factory=list)

    @property
    def connected(self) -> bool:
        return self.state == READY


class Engine:
    """link: connect() -> object with read(size, timeout) / write(data, timeout) / close().
    registry: anything with snapshot() -> dict (and optionally set_now_playing(appid))."""

    def __init__(self, registry, screens: dict, settings: Settings | None = None, connect=usb_connect, *,
                 clock=time.monotonic, wall=time.time, sleep=time.sleep, settings_error: str | None = None,
                 screen_errors=(), render=None):
        """render(screen, snapshot, screen_settings_or_None) -> packed frame; default render_frame. Override to run code screens."""
        self.registry, self.screens, self._render_fn = registry, dict(screens), render
        self.settings = settings or Settings()
        self._connect, self._clock, self._wall, self._sleep = connect, clock, wall, sleep
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._status = Status(settings_error=settings_error, screen_errors=list(screen_errors))
        self._sched = Scheduler(self.settings, self.screens)
        self._link = None
        self._dec = P.Decoder()
        self._seq = 0
        self._backoff = BACKOFF_MIN_S
        self._retry_at = 0.0
        self._ever_ready = False
        self._dirty = False            # a bound value changed since the last render (set by notify_change, any thread)
        self._watched = _UNSET         # what we last told registry.watch()
        self._bound: dict = {}         # screen id -> (Screen, bound paths)
        self._subscribe(registry)
        self._plan: IdlePlan | None = None
        self._idle = {"state": "none", "error": None}
        self._idle_uploaded: tuple | None = None   # ids of the last store the panel acknowledged
        self._idle_todo = False
        self._idle_seqs: set = set()
        self._idle_left = 0
        self._asleep_at: float | None = None
        self._retry_now = False      # after wake(): the first connection loss is retried at once (the port often died in the suspend)
        self._reset_session()

    # -- public API ---------------------------------------------------------------
    def status(self) -> dict:
        with self._lock:
            s = self._status
            d = {k: getattr(s, k) for k in s.__dataclass_fields__}
            d["screen_errors"] = list(s.screen_errors)
            d["connected"] = s.connected
            was = self._sched.profile
            d["profile"] = self._sched.select(self._clock())   # "normal" or "ingame"; also right while disconnected
            if d["profile"] != was:
                self._next_render = 0.0   # the switch happened here, not in a render: draw the new profile now
            d["pinned"] = self.settings.profile(d["profile"])["pinned"]   # the pin of the profile in use
            d["idle"] = self._idle_status()
            if s.state == DISCONNECTED:
                d["retry_in_s"] = round(max(0.0, self._retry_at - self._clock()), 3)
            return d

    def notify_change(self) -> None:
        """A watched value changed (the registry calls this from its polling thread): render as soon as the panel is free."""
        self._dirty = True

    def _subscribe(self, registry) -> None:
        fn = getattr(registry, "subscribe", None)
        if fn is not None:
            fn(self.notify_change)

    def _watch(self, keys) -> None:
        """Tell the registry what the current screen displays (None = everything, empty = nothing)."""
        if keys != self._watched:
            self._watched = keys
            fn = getattr(self.registry, "watch", None)
            if fn is not None:
                fn(keys)

    def set_now_playing(self, appid) -> None:
        """Game started (appid) or exited (None). Raises ValueError for a bad appid, changing nothing."""
        with self._lock:
            fn = getattr(self.registry, "set_now_playing", None)
            if fn is not None:
                fn(appid)
            self._sched.set_now_playing(appid)
            self._status.now_playing = appid or None
            self._next_render = 0.0

    def update_settings(self, settings: Settings) -> None:
        with self._lock:
            if settings.idle_timeout_s != self.settings.idle_timeout_s:
                self._need_mode = True   # MODE carries idle_timeout_s
            self.settings = settings
            self._sched.update(settings, self._clock())
            self._next_render = 0.0

    def set_idle_plan(self, plan: IdlePlan | None, force: bool = False) -> None:
        """What the panel's idle store should hold. Uploaded on connect, and again whenever it changes (or `force`).

        Nothing is sent when the plan has no blob (nothing selected, or it did not compile) or the panel cannot take it."""
        with self._lock:
            old, self._plan = self._plan, plan or IdlePlan()
            if not force and old is not None and old.blob == self._plan.blob:
                return
            self._idle_abort()
            self._idle_todo = True
            self._next_render = 0.0

    def _idle_status(self) -> dict:
        """No blob and no secret in here: ids, sizes, state, error text."""
        plan, up = self._plan, self._idle
        st = self._status
        state, err = up["state"], up["error"]
        if plan is None or (not plan.ids and plan.error is None):
            state, err = "none", None
        elif plan.error:
            state, err = "error", plan.error
        elif st.state == READY and not (st.caps or 0) & P.CAP_IDLE_STORAGE:
            state, err = "unsupported", "this firmware has no idle storage: update the firmware"
        elif st.state != READY and state in ("ok", "uploading"):
            state = "pending"
        return {"state": state, "error": err, "ids": list(plan.ids) if plan else [],
                "on_panel": list(self._idle_uploaded or ()), "modes": list(plan.modes) if plan else [],
                "bytes": plan.size if plan else 0, "free": st.idle_flash_free, "timeout_s": self.settings.idle_timeout_s}

    def set_registry(self, registry) -> None:
        """Swap the data registry (source settings changed). The caller stops the old one."""
        with self._lock:
            old, self.registry = self.registry, registry
            fn = getattr(old, "unsubscribe", None)
            if fn is not None:
                fn(self.notify_change)
            self._subscribe(registry)
            self._watched = _UNSET
            fn = getattr(registry, "set_now_playing", None)
            if fn is not None and self._sched.now_playing:
                fn(self._sched.now_playing)
            self._next_render = 0.0

    def set_screens(self, screens: dict, errors=()) -> None:
        with self._lock:
            self.screens = dict(screens)
            self._bound.clear()
            self._status.screen_errors = list(errors)
            self._sched.set_available(self.screens)
            self._next_render = 0.0

    def pause(self) -> None:
        """Let go of the port (no MODE PANEL, no frames) so someone else, e.g. the firmware updater, can use it.

        Blocks until the engine step in flight is done. The panel drops our session on its own after its host timeout.
        """
        with self._lock:
            if self._status.state == STOPPED:
                return
            if self._link is not None:
                try:
                    self._link.close()
                except Exception:  # noqa: BLE001
                    pass
                self._link = None
            self._reset_session()
            self._dec = P.Decoder()
            self._status.state = PAUSED

    def resume(self) -> None:
        """Reconnect right away (fresh handshake, full redraw). No-op unless paused."""
        with self._lock:
            if self._status.state == PAUSED:
                self._status.state = DISCONNECTED
                self._retry_at, self._backoff = 0.0, BACKOFF_MIN_S

    def prepare_sleep(self, timeout: float = SLEEP_ACK_S) -> bool:
        """The host is about to suspend (logind PrepareForSleep): put the panel on its idle screens now.

        Sends MODE PANEL and waits up to `timeout` for the ACK (the panel answers before it draws, so ~ms unless it is
        mid-refresh). True once ACKed. False when there is no panel session or the ACK did not come; either way the
        engine stops talking to the panel until wake(), so a lost port cannot pull it back out of idle.
        """
        with self._lock:
            st = self._status
            if st.state in (STOPPED, PAUSED):
                return False
            if self._asleep_at is not None:
                return True
            self._asleep_at, st.asleep = self._clock(), True
            return st.state == READY and self._idle_now(timeout)

    def wake(self) -> None:
        """The host resumed (PrepareForSleep(false), or a suspend that never happened): panel back to host rendering.

        Reconnects at once if the port went away meanwhile, then MODE HOST and a full redraw (the glass shows an idle picture).
        """
        with self._lock:
            if self._asleep_at is None:
                return
            self._asleep_at, self._status.asleep = None, False
            self._retry_at, self._backoff, self._retry_now = 0.0, BACKOFF_MIN_S, True
            if self._status.state == READY:
                self._reset_session()   # forget the panel's contents: MODE HOST, then everything again
                self._idle_todo = self._idle_todo or self._idle["state"] == "pending"

    def _idle_now(self, timeout: float) -> bool:
        """MODE PANEL + wait for its ACK, serving the replies of frames still in flight. Never raises."""
        try:
            seq = self._next_seq()
            naks = self._status.naks
            self._link.write(P.mode(seq, P.MODE_PANEL), 1.0)
            self._inflight[seq] = (0, "idle_now", self._clock())
            for _ in range(max(1, round(timeout / POLL_S))):
                self._read(self._clock())
                if self._link is None:   # the read dropped the connection
                    return False
                if seq not in self._inflight:
                    return self._status.naks == naks
                self._sleep(POLL_S)
        except (OSError, TimeoutError, ValueError, AttributeError, P.ProtocolError, ConnectionError) as e:
            self._set_error(f"panel idle-now failed: {type(e).__name__}: {e}")
        return False

    def start(self) -> None:
        """Run in a daemon thread. stop() ends it."""
        with self._lock:
            if self._thread:
                return
            self._stop.clear()
            if self._status.state == STOPPED:
                self._status.state = DISCONNECTED
            self._thread = threading.Thread(target=self.run, name="inkterface-engine", daemon=True)
            self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        t, self._thread = self._thread, None
        if t:
            t.join(timeout)
        with self._lock:
            self._shutdown()

    def run(self) -> None:
        """Blocking loop until stop(). Survives any error in a step: it drops the link and reconnects."""
        while not self._stop.is_set():
            with self._lock:
                try:
                    self.step()
                except Exception as e:  # noqa: BLE001 - the loop must outlive a bug
                    log.exception("engine step failed")
                    self._drop(self._clock(), e)
            self._sleep(POLL_S)
        with self._lock:
            self._shutdown()

    # -- one tick -----------------------------------------------------------------
    def step(self) -> None:
        with self._lock:
            now = self._clock()
            st = self._status
            if self._asleep_at is not None:   # panel is on its idle screens: no reconnect, no heartbeat, no frame; just drain it
                if now - self._asleep_at >= WAKE_FAILSAFE_S:
                    log.warning("engine: no wake %.0f s after prepare_sleep: waking up", WAKE_FAILSAFE_S)
                    self.wake()
                elif self._link is not None:
                    try:
                        self._read(now)
                    except (OSError, TimeoutError, P.ProtocolError, ConnectionError) as e:
                        self._drop(now, e)
                return
            if st.state in (DISCONNECTED, REFUSED):
                if now >= self._retry_at:
                    self._open(now)
                return
            if st.state in (STOPPED, PAUSED):
                return
            try:
                self._read(now)
                if st.state == HANDSHAKE:
                    self._handshake_timers(now)
                elif st.state == READY:
                    self._ready(now)
            except (OSError, TimeoutError, P.ProtocolError, ConnectionError) as e:
                self._drop(now, e)

    # -- connection ---------------------------------------------------------------
    def _reset_session(self) -> None:
        self._outq: deque = deque()  # (seq, wire, kind) not yet written
        self._inflight: dict = {}  # seq -> (wire len, kind, sent_at)
        self._window = 8192
        self._last_sent: bytes | None = None  # what the panel's shadow buffer holds (wire polarity)
        self._shown: str | None = None
        self._force_full = True
        self._partials = 0
        self._last_full_at: float | None = None   # clock time the panel acknowledged its last full refresh (ghost-clear timer)
        self._next_render = 0.0   # max-staleness deadline: render at the latest then (screen.refresh_s after the last render)
        self._min_next = 0.0      # earliest next render for a change event (MIN_RENDER_GAP_S after the last render)
        self._watch(frozenset())  # no panel: nobody needs fast sampling
        self._last_tx = 0.0
        self._naks_in_row = 0
        self._deferred = False
        self._changed_at = float("-inf")   # when a render last had something to send (ghost-clear scheduling)
        self._sample_on_free = False   # a refresh was just acknowledged: sample the fast sources before the next render
        self._need_mode = True
        self._hello_sent_at = self._hello_started = 0.0
        self._hellos_expected = 0
        self._pending_full = False
        self._idle_seqs, self._idle_left = set(), 0
        if self._idle["state"] == "uploading":
            self._idle = {"state": "pending", "error": None}

    def _open(self, now: float) -> None:
        st = self._status
        try:
            self._link = self._connect()
            self._dec = P.Decoder()
            self._reset_session()
            st.state, st.retry_in_s = HANDSHAKE, None
            self._hello_started = now
            self._send_hello(now)
        except (OSError, TimeoutError) as e:
            self._drop(now, e)

    def _send_hello(self, now: float) -> None:
        self._link.write(P.hello_request(self._next_seq()), 5.0)
        self._hello_sent_at = self._last_tx = now
        self._hellos_expected += 1

    def _handshake_timers(self, now: float) -> None:
        if now - self._hello_started >= HELLO_TIMEOUT_S:
            raise ConnectionError(f"no HELLO from panel after {HELLO_TIMEOUT_S:.0f} s")
        if now - self._hello_sent_at >= HELLO_RETRY_S:
            self._send_hello(now)

    def _drop(self, now: float, err, delay: float | None = None) -> None:
        """Connection lost or refused: close, forget the panel's state, retry after the backoff."""
        st = self._status
        if self._link is not None:
            try:
                self._link.close()
            except Exception:  # noqa: BLE001 - already failing
                pass
            self._link = None
        self._set_error(f"{type(err).__name__}: {err}" if not isinstance(err, str) else err)
        self._reset_session()
        self._dec = P.Decoder()
        st.state = DISCONNECTED if delay is None else REFUSED
        wait = 0.0 if self._retry_now and delay is None else self._backoff if delay is None else delay
        self._retry_now = False
        self._retry_at = now + wait
        if delay is None and wait:
            self._backoff = min(self._backoff * 2, BACKOFF_MAX_S)

    def _set_error(self, msg: str) -> None:
        self._status.last_error, self._status.last_error_at = msg, self._wall()
        log.warning("engine: %s", msg)

    def _shutdown(self) -> None:
        """Hand the panel back to its own screens (MODE PANEL) and close the port."""
        if self._link is not None:
            if self._status.state == READY and self._asleep_at is None:
                self._idle_now(SHUTDOWN_ACK_S)
            try:
                self._link.close()
            except Exception:  # noqa: BLE001
                pass
            self._link = None
        self._watch(frozenset())
        self._asleep_at, self._status.asleep = None, False
        self._status.state = STOPPED

    # -- reading ------------------------------------------------------------------
    def _read(self, now: float) -> None:
        data = self._link.read(4096, 0)
        for f in self._dec.feed(data) if data else []:
            if self._link is None:  # a frame above dropped the connection
                return
            if f.type == P.T_HELLO:
                self._on_hello(f, now)
            elif f.type == P.T_ACK and f.payload:
                self._on_ack(f.payload[0], now)
            elif f.type == P.T_NAK and len(f.payload) >= 2:
                self._on_nak(f.payload[0], f.payload[1], now)
            elif f.type == P.T_LOG:
                log.info("panel: %s", f.payload.decode("utf-8", "replace").rstrip())

    def _on_hello(self, f: P.Frame, now: float) -> None:
        st = self._status
        try:
            h = P.parse_hello(f.payload)
        except P.ProtocolError as e:
            self._drop(now, e)
            return
        if h.proto_ver != P.PROTO_VER or (h.width, h.height) != (P.WIDTH, P.HEIGHT):
            st.fw_version, st.protocol_version, st.panel_size = h.fw_ver, h.proto_ver, (h.width, h.height)
            self._drop(now, f"panel firmware {h.fw_ver!r} speaks protocol v{h.proto_ver} at {h.width}x{h.height}; "
                            f"this plugin needs v{P.PROTO_VER} at {P.WIDTH}x{P.HEIGHT}: update the firmware", delay=REFUSED_RETRY_S)
            return
        solicited, self._hellos_expected = self._hellos_expected > 0, max(0, self._hellos_expected - 1)
        if st.state == READY and not solicited:  # the panel lost our session (reset or host timeout)
            self._outq.clear()
            self._inflight.clear()
            self._last_sent, self._force_full, self._need_mode = None, True, True
            self._idle_seqs, self._idle_left = set(), 0
            self._idle_todo = self._idle_todo or self._idle["state"] == "uploading"   # restart an interrupted upload
        st.fw_version, st.protocol_version, st.panel_size = h.fw_ver, h.proto_ver, (h.width, h.height)
        st.caps, st.idle_flash_free = h.caps, h.idle_flash_free
        self._window = max(h.rx_window, 1)
        if st.state != READY and self._ever_ready:
            st.reconnects += 1
        if st.state != READY:
            self._idle_todo = True   # every new connection sends the idle store again (the panel may have been reflashed)
            st.last_error = st.last_error_at = None   # a good HELLO ends whatever broke the old link; later errors set it again
        st.state, self._ever_ready = READY, True
        self._backoff = BACKOFF_MIN_S

    def _on_ack(self, seq: int, now: float) -> None:
        self._retry_now = False   # the link answers: a later loss is an ordinary one
        item = self._inflight.pop(seq, None)
        if item is not None and item[1] == "idle" and seq in self._idle_seqs:
            self._idle_seqs.discard(seq)
            self._idle_left -= 1
            if self._idle_left == 0:
                self._idle = {"state": "ok", "error": None}
                self._idle_uploaded = self._plan.ids
                log.info("idle layouts on the panel: %s (%d bytes)", ", ".join(self._plan.ids), self._plan.size)
            return
        if item is None or item[1] != "refresh":
            return
        st = self._status
        self._naks_in_row = 0
        self._sample_on_free = True
        st.last_frame_at = self._wall()
        if self._pending_full:
            st.full_refreshes += 1
            self._partials, self._last_full_at = 0, now
        else:
            st.partial_refreshes += 1
            self._partials += 1

    def _on_nak(self, seq: int, reason: int, now: float) -> None:
        item = self._inflight.pop(seq, None)
        if item is None:
            return
        if item[1] == "idle":   # the idle upload failed: not a display problem, so no redraw and no NAK streak
            if seq in self._idle_seqs:
                name = P.NAK_NAMES.get(reason, reason)
                self._idle_abort(state="error", error=f"the panel refused the idle layouts: {name}")
                self._set_error(f"panel NAK {name} for idle layouts")
            return
        st = self._status
        st.naks += 1
        self._naks_in_row += 1
        self._set_error(f"panel NAK {P.NAK_NAMES.get(reason, reason)} for {item[1]}")
        if self._naks_in_row >= MAX_CONSECUTIVE_NAKS:
            raise ConnectionError(f"panel rejected {self._naks_in_row} frames in a row")
        # What the panel holds is unknown now: drop the rest of this batch and redraw everything.
        self._outq.clear()
        self._last_sent, self._force_full, self._need_mode = None, True, True
        self._next_render = 0.0

    # -- ready: timers, render, send ---------------------------------------------
    def _ready(self, now: float) -> None:
        for seq, (_, kind, sent) in list(self._inflight.items()):
            if now - sent > {"refresh": REFRESH_TIMEOUT_S, "idle": IDLE_ACK_TIMEOUT_S}.get(kind, ACK_TIMEOUT_S):
                raise TimeoutError(f"panel did not answer {kind} (seq {seq})")
        self._pump(now)
        due = self._due(now)
        if self._outq or self._inflight:  # one batch at a time: data changes meanwhile are coalesced
            if due and any(i[1] == "refresh" for i in self._inflight.values()):
                self._deferred = True
            return
        if self._sample_on_free:   # the panel is free: take the freshest value now, not one up to fast_interval (150 ms) old
            self._sample_on_free = False
            self._sample_now()
            due = self._due(now)
        if self._need_mode:
            if (self._status.caps or 0) & P.CAP_IDLE_STORAGE:   # older firmware NAKs the extended MODE
                self._queue("mode", P.mode_ex, P.MODE_HOST, self.settings.idle_timeout_s)
            else:
                self._queue("mode", P.mode, P.MODE_HOST)
            self._need_mode = False
        elif self._idle_todo:
            self._idle_start()
        elif due:
            self._render_and_queue(now)
        if not self._outq and now - self._last_tx >= P.HEARTBEAT_INTERVAL_S:
            self._queue("heartbeat", P.heartbeat)
            self._status.heartbeats_sent += 1
        self._pump(now)

    def _due(self, now: float) -> bool:
        """Render now: the staleness deadline passed, a watched value changed, or a debounced profile switch is now allowed."""
        return now >= self._next_render or (self._dirty and now >= self._min_next) or self._sched.switch_pending(now)

    def _sample_now(self) -> None:
        fn = getattr(self.registry, "sample_now", None)
        if fn is None:
            return
        try:
            fn()   # a changed watched key calls notify_change() on this thread
        except Exception:  # noqa: BLE001 - sampling is best effort; the registry's own threads keep polling
            log.exception("registry sample_now failed")

    def _next_seq(self) -> int:
        s = self._seq
        self._seq = (self._seq + 1) & 0xFF
        return s

    def _queue(self, kind: str, build, *args) -> None:
        seq = self._next_seq()
        self._outq.append((seq, build(seq, *args), kind))

    def _pump(self, now: float) -> None:
        """Write queued frames, keeping <= rx_window unacknowledged wire bytes (the panel drops overflow)."""
        while self._outq:
            seq, wire, kind = self._outq[0]
            if self._inflight and (seq in self._inflight or sum(v[0] for v in self._inflight.values()) + len(wire) > self._window):
                return
            self._outq.popleft()
            self._link.write(wire, 5.0)
            self._inflight[seq] = (len(wire), kind, now)
            self._last_tx = now
            self._status.bytes_sent += len(wire)
            if kind == "rect":
                self._status.rects_sent += 1

    # -- idle store upload ---------------------------------------------------------
    def _idle_abort(self, state: str | None = None, error: str | None = None) -> None:
        """Forget queued chunks of the current upload (inflight ones still get answered and are ignored)."""
        if self._idle_seqs:
            self._outq = deque(x for x in self._outq if x[0] not in self._idle_seqs)
        self._idle_seqs, self._idle_left = set(), 0
        self._idle_todo = False if state else self._idle_todo
        if state:
            self._idle = {"state": state, "error": error}
        elif self._idle["state"] == "uploading":
            self._idle = {"state": "pending", "error": None}

    def _idle_start(self) -> None:
        """Queue the whole ISTR blob as IDLE_LAYOUT chunks. Refuses (status says why) before sending anything."""
        self._idle_todo = False
        st, plan = self._status, self._plan
        if plan is None or plan.blob is None:   # nothing selected, or it did not compile (status shows plan.error)
            self._idle = {"state": "none", "error": None}
            return
        if not (st.caps or 0) & P.CAP_IDLE_STORAGE:
            return   # status: unsupported
        if plan.needs_gray4 and not (st.caps or 0) & P.CAP_GRAY4:
            self._idle = {"state": "error", "error": "a selected idle screen needs gray4 and this panel has none"}
            return
        cap = min(IF.MAX_STORE_BYTES, st.idle_flash_free or IF.MAX_STORE_BYTES)
        if len(plan.blob) > cap:
            self._idle = {"state": "error", "error": f"the idle layouts need {len(plan.blob)} bytes, the panel stores {cap}"}
            return
        total = len(plan.blob)
        for off in range(0, total, IDLE_CHUNK):
            seq = self._next_seq()
            self._outq.append((seq, P.idle_layout(seq, total, off, plan.blob[off:off + IDLE_CHUNK]), "idle"))
            self._idle_seqs.add(seq)
        self._idle_left = len(self._idle_seqs)
        self._idle = {"state": "uploading", "error": None}

    def _render_and_queue(self, now: float) -> None:
        st = self._status
        self._dirty = False   # cleared before the snapshot: a change landing meanwhile re-arms it
        self._min_next = now + MIN_RENDER_GAP_S
        cur = self._sched.current(now)
        st.current_screen = cur
        screen = self.screens.get(cur)
        self._watch(frozenset() if screen is None else self._bound_paths(screen))
        if screen is None:
            self._next_render = now + 1.0
            if st.last_error != "no active screens available":
                self._set_error("no active screens available")
            return
        if cur != self._shown:
            self._shown, self._force_full = cur, True
        try:
            frame = self._render(screen)
        except Exception as e:  # noqa: BLE001 - a bad screen must not stop the engine
            self._set_error(f"render {cur}: {type(e).__name__}: {e}")
            self._next_render = now + max(screen.refresh_s, 1.0)
            return
        self._next_render = now + screen.refresh_s
        st.frames_rendered += 1
        if self._last_sent is None:
            rects = _FULL_BANDS
        else:
            rects = D.diff_rects(self._last_sent, frame)
        full = self._force_full or self._last_sent is None
        phase = ghost_phase(self.settings.ghost_clear_minutes, self._partials, self._since_full(now))
        if not full and phase in ("due", "forced"):
            # The full refresh blocks the panel for 4 s and a value that changes meanwhile waits for it. So while changes keep
            # coming it is deferred to the first quiet moment, but at most until GHOST_DEFER_LIMIT times the interval
            # (ghosting must still be cleared).
            quiet = not rects and now - self._changed_at >= GHOST_QUIET_S
            if quiet or phase == "forced":
                full = True
            elif rects:
                st.ghost_deferred += 1
        if rects or full:
            self._changed_at = now
        if not rects and not full:
            st.unchanged_ticks += 1
            self._deferred = False
            return
        if self._deferred:
            st.coalesced += 1
            self._deferred = False
        for r in rects:
            self._queue("rect", P.rect, *r, D.crop(frame, r))
        self._queue("refresh", P.refresh, full)
        self._pending_full = full
        self._last_sent, self._force_full = frame, False

    def _since_full(self, now: float) -> float:
        """Seconds since the last acknowledged full refresh (0 before the first). A clock that went backwards restarts the timer."""
        if self._last_full_at is None:
            return 0.0
        if now < self._last_full_at:
            self._last_full_at = now
        return now - self._last_full_at

    def _bound_paths(self, screen):
        hit = self._bound.get(screen.id)
        if hit is None or hit[0] is not screen:
            hit = self._bound[screen.id] = (screen, screen.bound_paths())
        return hit[1]

    def _render(self, screen) -> bytes:
        snap = self.registry.snapshot()
        over = self.settings.screen_settings.get(screen.id)
        try:
            raw = (self._render_fn or render_frame)(screen, snap, over)
        except ScreenError as e:  # bad per-screen settings: show the screen with defaults
            self._set_error(f"settings for {screen.id}: {e.errors[0]}")
            raw = (self._render_fn or render_frame)(screen, snap, None)
        return raw.translate(_INVERT)
