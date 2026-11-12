# Host runtime engine

`decky/py_modules/inkterface_core/engine/` keeps the panel showing the current **active** screen:
it polls the data sources, renders the screen, sends only what changed, refreshes, and keeps the
link alive. No Decky imports; stdlib + Pillow. Wire format: `docs/usb-protocol-v1.md`.

```
Registry.snapshot() -> screens.render_frame() -> invert (1 = black) -> diff vs last sent
        -> RECT frames (window-limited) -> REFRESH partial|full -> ACK
```

| File | Role |
|------|------|
| `settings.py` | `Settings`: JSON file, validated, atomic save |
| `scheduler.py` | `Scheduler`: which screen is current (pin / now-playing / rotation) |
| `engine.py` | `Engine` (the loop), `Status`, `usb_connect`, `load_screens` |
| `memlink.py` | `EmulatedLink`: in-memory serial link to `EmulatedPanel` with the real panel's timing (tests, `run_engine.py --emulator`) |

Dev tool: `python3 decky/tools/run_engine.py --emulator out/ --seconds 30` (PNG per refresh) or without
`--emulator` against a real panel. Tests: `python3 -m pytest decky/tests/engine`.

## Using it

```python
from inkterface_core.engine import Engine, Settings, load_screens
from inkterface_core.sources import default_registry

settings, settings_error = Settings.load(path)          # never raises; defaults + error text if the file is bad
screens, screen_errors = load_screens(builtin_dir, user_dir)
registry = default_registry(); registry.poll_due(force=True); registry.start()
engine = Engine(registry, screens, settings, settings_error=settings_error, screen_errors=screen_errors)
engine.start()                       # daemon thread; engine.run() blocks instead
engine.set_now_playing(570)          # SteamClient game-start event; None on exit
engine.update_settings(new)          # takes effect on the next tick
engine.status()                      # dict for the UI
engine.stop()                        # hands the display back to the panel (MODE PANEL)
```

`Engine(registry, screens, settings, connect=usb_connect, *, clock, wall, sleep, render=None)`. `render(screen, snapshot, screen_settings_or_None) -> packed frame` defaults to `screens.render_frame`; the Decky plugin passes one that runs code screens through the package store. `set_registry(r)` swaps the data registry (the caller stops the old one) and forwards the running game. `registry` is anything with
`snapshot() -> dict` (and optionally `set_now_playing`). `connect()` returns a port-like object
(`read(size, timeout)`, `write(data, timeout)`, `close()`), i.e. a `SerialPort` or an `EmulatedLink`.
`clock` (monotonic seconds), `wall` and `sleep` are injectable; tests drive `step()` with a fake clock.

## Lifecycle

`state` is one of:

| state | meaning |
|-------|---------|
| `disconnected` | no port. Retries after the backoff (below). |
| `handshake` | port open, HELLO request sent every 2 s, give up after 30 s (a freshly reset panel answers ~25 s late). |
| `ready` | HELLO accepted, panel in HOST mode, frames flowing. |
| `refused` | HELLO rejected. Port closed, **no frames sent**, retried every 60 s. |
| `paused` | `pause()` called (the firmware updater borrows the port): port closed, nothing sent, not even `MODE PANEL`. `resume()` reconnects at once with a full redraw. `fw_version` stays known. |
| `stopped` | `stop()` called. `start()` restarts it. |

`asleep` (a flag on any state, see below) is set between `prepare_sleep()` and `wake()`.

1. **Connect.** `usb_connect` finds the panel by USB VID `303a` (never the first ttyACM) and opens it with
   DTR and RTS low (`usb_link.open_port`), so opening does not reset the chip.
2. **HELLO.** Refused (state `refused`, message tells the user to update the firmware) when
   `proto_ver != 1` or the panel is not 648x480. Otherwise the engine records `fw_version`, takes `rx_window`
   as its flow-control window, and sends `MODE HOST`.
3. **First frame.** The panel's shadow buffer is unknown after a connect, so the first frame is the whole
   screen (10 RECT bands) plus `REFRESH full`.
4. **Run.** Tick loop below.
5. **Stop.** `stop()` sends `MODE PANEL` (the panel repaints its own screen, i.e. idle mode starts at once
   instead of after its 15 s host timeout), waits up to 0.5 s for the ACK so the frame is not lost to the close, and closes the port. If the host dies without `stop()`, the panel
   falls back to its own screen after 15 s without a valid frame.

An unsolicited HELLO while `ready` (panel reset, or its host timeout fired) is treated as a lost session:
`MODE HOST` again and a full redraw.

## Tick

`step()` is non-blocking; `run()` calls it every 20 ms. When `ready`:

1. Read and decode replies (ACK, NAK, HELLO, LOG).
2. Check timeouts: 5 s for any frame, 15 s for REFRESH. A timeout drops the connection (reconnect, below).
3. Write queued frames, keeping at most `rx_window` unacknowledged wire bytes (the panel silently drops
   overflow).
4. If nothing is queued or in flight and a render is due (the screen's `refresh_s` has elapsed since the last render, **or** a watched
   value changed since then and at least `MIN_RENDER_GAP_S` = 0.1 s passed; see "Event-driven rendering"):
   - ask the `Scheduler` for the current screen;
   - `registry.snapshot()`, `render_frame(screen, snapshot, per-screen settings)`, invert to wire polarity
     (screens pack 1 = white, the wire is 1 = black);
   - diff against the last frame sent (`diff.diff_rects`), queue one `RECT` per dirty rect, then one `REFRESH`.
5. If still idle and nothing was sent for 5 s: queue a `HEARTBEAT`. Any frame counts as liveness, so there are
   no heartbeats while frames flow.

Errors in step 4 (a screen that raises, the registry raising, no screens installed) are reported in
`last_error` and the engine keeps running; a broken widget already renders as `--` inside the screen engine.
Per-screen settings that fail validation fall back to that screen's defaults, with the error reported.

### Rate control

A batch (RECTs + REFRESH) is sent only when the previous batch is fully acknowledged, and the panel ACKs a
REFRESH only when the e-paper is done (partial ~1.8 s, full ~4.1 s). So there is never more than one refresh
pending, and no rendering happens while the panel is busy. Data that changes meanwhile is picked up by the
single render after the ACK (`coalesced` counts those renders). The next render is due when the data changes (at once after the ACK)
or `refresh_s` after the previous one started, whichever is first, so under continuous change the panel refreshes back to back at its own speed. Identical renders send nothing (`unchanged_ticks`).

### Event-driven rendering

`refresh_s` is the **max staleness**, not the render rate. The engine subscribes to the registry (`registry.subscribe(engine.notify_change)`)
and tells it which snapshot keys the current screen displays (`registry.watch(screen.bound_paths())`: `bind`s, `{template}` refs, a clock's
`time.now`/`time.tz`, the now-playing widget's `steam.now_playing`; a code screen watches everything). A poll that changes one of them sets a flag; the next
engine step (<= 20 ms) renders as soon as the panel is free. A change that arrives during a refresh waits for its ACK and is rendered once
(`coalesced`), however many changes came. When a refresh is acknowledged the engine first calls `registry.sample_now()` (if the registry has it), which polls the
fast-sampled sources it watches that were not polled in the last 20 ms, so a change from the last 150 ms of the refresh is in the very next render
instead of the one after. Watching also makes the registry sample those sources faster (`fast_interval`). A registry without
`subscribe`/`watch` (tests, other callers) just behaves as before: one render per `refresh_s`. `set_now_playing()` renders at once as well.

### Refresh and ghosting policy

| Situation | Rects | REFRESH |
|-----------|-------|---------|
| first frame after (re)connect, resync | full frame | full |
| screen switch | diff against the previous screen | full |
| ghost-clear **due** (below) and the screen is quiet | diff (possibly none) | full |
| ghost-clear **forced** (below), quiet or not | diff | full |
| otherwise | diff | partial |

The ghost-clear is gated on **time and count**. It is *due* once both hold: `ghost_clear_minutes` minutes have passed since the last
acknowledged full refresh, and at least `GHOST_MIN_PARTIALS` (30) acknowledged partials have happened since then (nothing to clear before).
Every full refresh (screen switch, first frame, reconnect, resync after a NAK, wake) restarts both the timer and the count. A full refresh blocks the
panel for 4 s, and a value that changes meanwhile waits for it, so the engine runs it at the first *quiet* render: nothing to send
and nothing sent for `GHOST_QUIET_S` (2 s). While changes keep coming (a screen like Telemetry never rests) it sends partials instead
(`ghost_deferred` in `status()`), but at the latest when `ghost_clear_minutes * GHOST_DEFER_LIMIT` (1.5) have passed, so ghosting
is always cleared. With the default 10 minutes: a quiet screen is cleared 10 minutes after its last full refresh, a busy one at 15 at the latest.
`0` disables it. The count gate is not a setting. `ghost_phase()` is the pure decision (off / wait / due / forced) and `Engine._since_full()` reads the
timer from the injectable engine clock; a clock that goes backwards restarts the timer instead of going negative. See [ghost-clear.md](ghost-clear.md) for why it is time-based.

### NAKs

A NAK logs `panel NAK <REASON> for <rect|refresh|mode|heartbeat>` in `last_error`, abandons the rest of the
batch, and forces `MODE HOST` plus a full redraw (the panel's state is unknown). After 3 NAKs in a row without
a successful refresh in between the connection is dropped and re-established with backoff.

## Sleep and wake

`prepare_sleep(timeout=3.0) -> bool` and `wake()` are called by the logind sleep hint (`sleep_watch.SleepWatcher`, docs/idle-mode.md
"Sleep and wake"); both are thread-safe.

* `prepare_sleep()` sends `MODE PANEL` straight away (not queued behind frames), reads the panel's replies until its ACK (a refresh in
  flight finishes first: the ACK comes after it) and returns True on the ACK. It returns False without waiting when there is no
  session (disconnected, handshake, unplugged), on a NAK, a dead port or after `timeout`; the engine is `asleep` in every case
  except `paused`/`stopped` (which return False and change nothing). Calling it again while asleep returns True.
* While `asleep` the engine sends nothing (no heartbeat, no frame, no reconnect, so nothing pulls the panel out of its pinned idle),
  only drains and logs what the panel sends. A port that dies meanwhile is closed but not reopened.
* `wake()` clears the flag, forgets what the panel shows (`MODE HOST`, then every band and a full refresh) and re-sends an interrupted
  idle store upload; a lost port is reopened at once (the 2 s backoff is skipped for that first retry).
* If `wake()` never comes, the engine wakes itself `WAKE_FAILSAFE_S` (20 s) after `prepare_sleep()` on its own monotonic clock,
  which does not run during a suspend, so this only triggers when no suspend happened.

## Reconnect

Any I/O error (`EIO` on unplug, write timeout), ACK timeout, HELLO timeout or too many NAKs closes the port,
forgets what the panel shows, and retries after a backoff of **2, 4, 8, 16, 32, 60, 60... s**. A successful
HELLO resets it to 2 s. After reconnecting the first frame is a full redraw. A missing panel (`no panel found`)
takes the same path, so plugging the panel in later just works. `reconnects` counts re-established
connections.

## Scheduler

Priority: **pinned screen > now-playing > rotation.**

- `pinned` (a screen id) wins over everything. An unknown id is ignored.
- `set_now_playing(appid)` switches to `now_playing_screen` when `auto_now_playing` is on and that screen is
  installed; `None` or `0` returns to the rotation.
- Rotation walks `rotation` (ids not installed are skipped) one slot per `rotation_interval_s`. While a pin or
  a game outranks it the rotation clock is held, so when it ends the rotation resumes on the same screen with
  the same time left. If the rotation lists nothing installed, the first installed screen is shown.

Everything depends only on the injected clock, so it is deterministic.

## Idle store upload

`Engine.set_idle_plan(plan)` hands the engine the ISTR blob to keep on the panel (`inkterface_core/idle/plan.py` builds it from the
user's idle screens). The engine sends it as `IDLE_LAYOUT` chunks (4000 B each, in order, inside the normal window/ACK flow) right after the
`MODE`, on every new connection and whenever the blob changes. It refuses before sending when: there is no blob (nothing selected or a screen
did not compile: `plan.error`), the firmware has no `idle_storage`, a gray4 layout meets a panel without gray4, or the blob is larger than the
panel's reported store. A NAK on any chunk (`BAD_DATA`, `STORAGE`) ends the upload with `idle.state = "error"`; it does not count towards the
display's NAK streak, does not trigger a redraw and is not retried by itself (change the selection or use `push_idle_layouts`). A lost
connection or a lost session restarts the upload from offset 0 on the next connect. The final chunk's ACK (flash write + read-back) gets 8 s.

## Status

`engine.status()` returns a dict (fresh copy each call, thread-safe):

| Field | Meaning |
|-------|---------|
| `connected` | `state == "ready"` |
| `state` | see Lifecycle |
| `fw_version`, `protocol_version`, `panel_size`, `caps`, `idle_flash_free` | from HELLO (`caps` bits: `proto.CAP_GRAY4`, `CAP_IDLE_STORAGE`) |
| `idle` | the idle store upload: `state` (`none/pending/uploading/ok/error/unsupported`), `error`, `ids`, `on_panel`, `modes`, `bytes`, `free`, `timeout_s`. Set what to upload with `Engine.set_idle_plan(IdlePlan)`; see below |
| `current_screen`, `pinned`, `now_playing` | what is shown / pinned / the running game's appid |
| `last_error`, `last_error_at` | last problem (stays after recovery; `last_error_at` is wall-clock seconds) |
| `last_frame_at` | wall-clock time the last refresh finished |
| `retry_in_s` | seconds to the next connect attempt while `disconnected` |
| `frames_rendered` | renders done |
| `unchanged_ticks` | renders identical to what the panel shows (nothing sent) |
| `coalesced` | renders that folded in changes that arrived during a refresh |
| `ghost_deferred` | renders that sent a partial although the ghost-clear full refresh was due (changes were pending) |
| `rects_sent`, `bytes_sent` | RECT frames and wire bytes written since the handshake |
| `heartbeats_sent` | |
| `partial_refreshes`, `full_refreshes` | acknowledged refreshes |
| `naks` | NAKs received |
| `reconnects` | connections re-established after the first |
| `asleep` | the host is suspending: the panel was told to show its idle screens and the engine is silent until `wake()` |
| `settings_error` | why the settings file was replaced by defaults, if it was |
| `screen_errors` | screen packages that failed to load |

## Settings

One JSON file (`Settings.load(path)` / `settings.save(path)`). Missing file = defaults. Saves are atomic
(temp file in the same directory, fsync, rename). A file that is unreadable, not JSON, or fails validation
gives defaults plus an error string for `settings_error`, and the bad file is moved to `<path>.corrupt` so a
later save cannot destroy a hand-edited file. Validation is all-or-nothing and unknown keys are errors
(typos).

| Key | Type | Default | Notes |
|-----|------|---------|-------|
| `schema` | int | 1 | must be 1 when present |
| `rotation` | array of screen ids | `["telemetry", "thermals-power", "big-clock"]` | `[a-z0-9-]`, no duplicates |
| `rotation_interval_s` | int 5..86400 | 30 | |
| `pinned` | screen id or null | null | wins over rotation and now-playing |
| `auto_now_playing` | bool | true | |
| `now_playing_screen` | screen id | `"now-playing"` | |
| `idle_timeout_s` | int 15..3600 | 60 | silence before the panel shows its idle layout. Sent in `MODE HOST` (`mode u8, idle_timeout_s u16`) when the panel reports `idle_storage` (an older firmware gets the bare 1-byte `MODE`), and again when the value changes. The engine itself hands over via `MODE PANEL` on `stop()`. |
| `ghost_clear_minutes` | int 0..120 | 10 | minutes since the last full refresh before ghosting is cleared (also needs 30 partials); 0 = never. |
| `ingame_enabled` | bool | false | while on and a game runs, `ingame` replaces `rotation` / `rotation_interval_s` / `pinned` and `auto_now_playing` is not used. Switch within one tick, at most one switch per 3 s, rotation restarts at its first screen. `get_status().profile` says which (`normal` / `ingame`), `pinned` is that profile's pin. Not written to the file until used. |
| `ingame` | object or null | null | `{rotation, rotation_interval_s, pinned}`, same rules as the top-level keys. Kept when `ingame_enabled` goes off |
| `screen_settings` | object | `{}` | `{screen id: {setting key: value}}`, checked against each screen's own `settings` schema at render time |

```json
{
  "schema": 1,
  "rotation": ["telemetry", "big-clock"],
  "rotation_interval_s": 20,
  "pinned": null,
  "auto_now_playing": true,
  "ghost_clear_minutes": 10,
  "screen_settings": { "big-clock": { "time_format": "%H:%M" } }
}
```

## Limits

- The engine does not poll the registry: call `registry.start()` (threads) or `poll_due()` yourself. Without it there are no change events.
- The unit tests run against `EmulatedLink`, whose e-paper timing uses numbers measured on a real panel over USB.
- OTA is not handled by the engine; the plugin pauses the engine while a firmware install runs ([ota.md](ota.md)).
- Heartbeat/ACK timeouts are 5 s (the protocol suggests 1 s) because a Python tick can be late.
- `Scheduler` and the loop share one lock; `set_now_playing` and `update_settings` are safe from other threads.
