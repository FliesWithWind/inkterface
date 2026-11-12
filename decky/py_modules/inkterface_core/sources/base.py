"""Source interface and Registry: merges source fragments into one snapshot dict.

The snapshot is FLAT: keys are the canonical binding paths (`system.cpu.load`,
`system.cpu.load.history`, `steam.now_playing`, `weather`, ...). A flat dict is needed
because `system.cpu.load` is a number while `system.cpu.load.history` is a list.
Reserved key `_meta` carries per-source health. Stdlib only.
"""
from __future__ import annotations

import copy
import datetime
import json
import math
import logging
import threading
import time
from collections import deque

log = logging.getLogger(__name__)

HISTORY_LEN = 60
HISTORY_GAP = 0.8  # a fast-sampled source appends to its history at most once per interval * this


class SourceError(Exception):
    """A source could not produce data (network, parse, config). Message must not hold secrets."""

    def __init__(self, msg: str, status: int | None = None):
        super().__init__(msg)
        self.status = status  # HTTP status when the failure was one


class Source:
    """Subclass, set the class attributes, implement poll().

    paths:         top-level snapshot keys poll() may return (always present in the snapshot, None until known)
    history_paths: subset of paths that are numeric; the registry keeps a ring buffer per path
    stale_after / expire_after: seconds since the last good poll before values get the stale
                   flag / are replaced by None. Default 3x / 10x interval.
    fast_interval: sample this often (instead of `interval`) while a consumer watches one of `paths`
                   (Registry.watch). poll() must be cheap enough to run that often. History keeps its
                   `interval` cadence. None = always `interval`.
    instant:       poll() reads only local files and is quick, so Registry.set_now_playing may run it
                   on the caller's thread and the new value is in the snapshot before the call returns.
    """

    name = ""
    interval = 5.0
    paths: tuple = ()
    history_paths: tuple = ()
    max_backoff = 900.0
    stale_after: float | None = None
    expire_after: float | None = None
    fast_interval: float | None = None
    instant = False

    def __init__(self):
        self.last_error: str | None = None  # set by the registry: last poll failure, None after a success
        self.warning: str | None = None  # set by the source itself: degraded but still producing data

    def poll(self) -> dict:
        raise NotImplementedError


def touches(a: str, b: str) -> bool:
    """Same snapshot key, or one is a child of the other ('steam.now_playing' / 'steam.now_playing.name', 'steam.recent[0].name')."""
    return a == b or a.startswith((b + ".", b + "[")) or b.startswith((a + ".", a + "["))


class _Slot:
    def __init__(self, source: Source, history: int):
        self.source = source
        self.values: dict = {}
        self.updated: float | None = None  # registry clock at last good poll
        self.hist_at: float | None = None  # registry clock of the last history append
        self.poll_lock = threading.Lock()  # one poll of a source at a time (poll thread vs set_now_playing)
        self.fails = 0
        self.next_due = 0.0
        self.history = {p: deque(maxlen=history) for p in source.history_paths}
        self.wake = threading.Event()
        self.stale_after = source.stale_after if source.stale_after is not None else 3 * source.interval
        self.expire_after = source.expire_after if source.expire_after is not None else 10 * source.interval


def _merge(base: dict, frag: dict) -> None:
    """Overlay frag on base. None never overrides a value; dicts merge field by field; else later wins."""
    for k, v in frag.items():
        cur = base.get(k)
        if v is None:
            base.setdefault(k, None)
        elif isinstance(cur, dict) and isinstance(v, dict):
            cur.update({f: x for f, x in v.items() if x is not None or f not in cur})
        else:
            base[k] = v


class Registry:
    """Polls sources, isolates their failures, keeps history, builds the snapshot.

    Two drive modes: call poll_due() yourself (tests, simple loops), or start() one daemon
    thread per source so a slow network source never delays the 1 s system source.
    Source order matters: when several sources emit the same key, later ones win on conflicts.
    """

    def __init__(self, sources=(), clock=time.monotonic, history: int = HISTORY_LEN):
        self._clock = clock
        self._history = history
        self._lock = threading.RLock()
        self._slots: list[_Slot] = []
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._watch: frozenset | None = frozenset()
        self._listeners: list = []
        for s in sources:
            self.add(s)

    def add(self, source: Source) -> None:
        with self._lock:
            if any(s.source.name == source.name for s in self._slots):
                raise ValueError(f"duplicate source name {source.name!r}")
            self._slots.append(_Slot(source, self._history))

    def poll_due(self, force: bool = False) -> list[str]:
        """Poll every source whose time has come (all of them when force). Returns names polled."""
        now = self._clock()
        with self._lock:
            due = [s for s in self._slots if force or s.next_due <= now]
        for s in due:
            self._poll(s)
        return [s.source.name for s in due]

    # -- change events ---------------------------------------------------------------------

    def subscribe(self, callback) -> None:
        """callback() runs on the polling thread after a poll changed a watched key. Keep it quick; it must not raise."""
        with self._lock:
            self._listeners.append(callback)

    def unsubscribe(self, callback) -> None:
        with self._lock:
            self._listeners = [c for c in self._listeners if c != callback]

    def watch(self, keys) -> None:
        """Snapshot keys a consumer displays: None = everything, empty = nothing (the default).

        A poll that changes a watched key calls the subscribers. Sources with a `fast_interval` that own a watched
        key are sampled that often; the others, and every source while nothing is watched, at `interval`."""
        keys = None if keys is None else frozenset(keys)
        with self._lock:
            if keys == self._watch:
                return
            self._watch = keys
            for slot in self._slots:
                if slot.updated is not None and self._interval(slot) < slot.source.interval:
                    slot.next_due = min(slot.next_due, slot.updated + self._interval(slot))
                    slot.wake.set()

    def sample_now(self, min_age: float = 0.02) -> list[str]:
        """Poll the fast-sampled sources somebody watches whose last poll is at least `min_age` s old; returns their names.

        The engine calls it when the panel becomes free, so the render shows the freshest value instead of one up to
        `fast_interval` old (a change from the last 150 ms of a refresh would otherwise wait for the next refresh too)."""
        now = self._clock()
        with self._lock:
            due = [s for s in self._slots
                   if s.updated is not None and now - s.updated >= min_age and self._interval(s) < s.source.interval]
        for s in due:
            self._poll(s)
        return [s.source.name for s in due]

    def _watching(self, paths) -> bool:
        w = self._watch
        return bool(paths) and (w is None or any(touches(k, p) for k in w for p in paths))

    def _interval(self, slot: _Slot) -> float:
        src = slot.source
        return min(src.fast_interval, src.interval) if src.fast_interval and self._watching(src.paths) else src.interval

    def _poll(self, slot: _Slot) -> None:
        with slot.poll_lock:
            changed = self._poll_locked(slot)
        if changed:
            for cb in list(self._listeners):
                try:
                    cb()
                except Exception:  # noqa: BLE001 - a bad subscriber must not stop the poll thread
                    log.exception("registry subscriber failed")

    def _poll_locked(self, slot: _Slot) -> bool:
        """One poll. True when it changed a key somebody watches."""
        src = slot.source
        try:
            frag = src.poll()
            if not isinstance(frag, dict):
                raise TypeError(f"poll() returned {type(frag).__name__}, expected dict")
        except Exception as e:  # isolation: one source must never take down the others
            err = str(e) or type(e).__name__
            with self._lock:
                src.last_error = err
                slot.fails += 1
                delay = min(src.interval * 2 ** min(slot.fails, 16), src.max_backoff)
                slot.next_due = self._clock() + max(delay, src.interval)
            return False
        with self._lock:
            now = self._clock()
            old = slot.values
            changed = {k for k, v in frag.items() if k not in old or old[k] != v}
            changed |= {k + ".history" for k in changed if k in slot.history}
            interval = self._interval(slot)
            slot.values = frag
            slot.updated = now
            slot.fails = 0
            slot.next_due = now + interval
            src.last_error = None
            # fast-sampled sources keep the history at their normal cadence (60 samples = 60 intervals)
            if interval >= src.interval or slot.hist_at is None or now - slot.hist_at >= HISTORY_GAP * src.interval:
                slot.hist_at = now
                for p, ring in slot.history.items():
                    v = frag.get(p)
                    if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
                        ring.append(v)
            w = self._watch
            return bool(changed and self._listeners) and (w is None or any(touches(k, c) for k in w for c in changed))

    def set_now_playing(self, appid) -> None:
        """Forward the frontend's SteamClient event to every source that tracks it and refresh them now.

        `instant` sources are polled right here, so the new value is in the snapshot when this returns; the others
        (network) are woken and poll on their own thread."""
        for slot in list(self._slots):
            fn = getattr(slot.source, "set_now_playing", None)
            if fn is None:
                continue
            fn(appid)  # ValueError for a bad appid propagates before any state changes
            if slot.source.instant:
                self._poll(slot)
            else:
                with self._lock:
                    slot.next_due = 0.0
                slot.wake.set()

    def snapshot(self) -> dict:
        now = self._clock()
        out: dict = {}
        meta: dict = {}
        stale_paths: list[str] = []
        with self._lock:
            for slot in self._slots:
                src = slot.source
                age = None if slot.updated is None else now - slot.updated
                stale = age is None or age > slot.stale_after
                expired = age is None or age > slot.expire_after
                frag = {p: None for p in src.paths} if expired else copy.deepcopy(slot.values)
                for p in src.paths:
                    frag.setdefault(p, None)
                _merge(out, frag)
                for p, ring in slot.history.items():
                    out[p + ".history"] = list(ring)
                if stale:
                    stale_paths.extend(src.paths)
                meta[src.name] = {
                    "stale": stale,
                    "age": None if age is None else round(age, 3),
                    "error": src.last_error,
                    "warning": src.warning,
                    "paths": list(src.paths),
                }
        out["_meta"] = {"sources": meta, "stale": sorted(set(stale_paths))}
        return out

    def start(self) -> None:
        if self._threads:
            return
        self._stop.clear()
        for slot in self._slots:
            t = threading.Thread(target=self._run, args=(slot,), name=f"src-{slot.source.name}", daemon=True)
            t.start()
            self._threads.append(t)

    def _run(self, slot: _Slot) -> None:
        while not self._stop.is_set():
            slot.wake.wait(max(0.0, slot.next_due - self._clock()))
            slot.wake.clear()
            if self._stop.is_set():
                break
            if self._clock() >= slot.next_due:
                self._poll(slot)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        for s in self._slots:
            s.wake.set()
        for t in self._threads:
            t.join(timeout)
        self._threads = []


def jsonable(obj):
    """json.dumps default= hook: datetimes become ISO strings (snapshot['time.now'] is an aware datetime)."""
    if isinstance(obj, (datetime.datetime, datetime.date)):
        return obj.isoformat()
    raise TypeError(f"not JSON serialisable: {type(obj).__name__}")


def dumps(snapshot: dict, **kw) -> str:
    return json.dumps(snapshot, default=jsonable, **kw)
