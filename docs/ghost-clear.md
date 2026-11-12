# Ghost-clear: the full refresh that clears ghosting

A partial refresh leaves a faint trace of the previous picture ("ghost"). A **full** refresh (4 s, the whole panel flashes) wipes it.
This page is about how often the engine does that on its own. Code: `ghost_phase()` and `Engine._render_and_queue()` in
`decky/py_modules/inkterface_core/engine/engine.py`; behaviour table in [engine.md](engine.md).

## Behaviour

Ghost clearing is time-based, not a count of partials: Telemetry never rests and does ~2 partials a second, so a count of 30..75
would flash every 44 s, about 80 flashes an hour.

| | |
|---|---|
| Setting | `ghost_clear_minutes`, integer 0..120, **default 10**. 0 = never. |
| Due | both: at least `ghost_clear_minutes` minutes since the last acknowledged full refresh **and** at least 30 partials since then (`GHOST_MIN_PARTIALS`, not a setting; with fewer than 30 partials there is nothing worth clearing). |
| Where | at the first *quiet* render (nothing to send, nothing sent for 2 s), so the flash does not delay a value that is changing. |
| Hard cap | if the screen never rests, forced at 1.5 x the setting (`GHOST_DEFER_LIMIT`): 15 min with the default. Never skipped forever. |
| Timer reset | every full refresh: screen switch (rotation, pin, now-playing), first frame, reconnect, resync after a NAK, wake from suspend. |
| Clock | the engine's injectable monotonic clock; a clock that goes backwards restarts the timer instead of going negative. |
| UI | Settings page: *Full refresh to clear ghosting*: Never / every 5 / 10 (default) / 15 / 30 min / hour / 2 hours. |

### Why 10 minutes

* Telemetry ran on a Steam Machine panel with no visible ghosting. That was with a clear every 44 s, so it says that 30..75
  partials are harmless, not that 1000 are. A short default would keep the flash; a very long one is untested on the glass.
* 10 minutes is ~1100 partials on Telemetry, about 15 times what had been left on the glass in that test, and still a rare event: at most 6 flashes an hour on a screen
  that rests, 4 an hour (cap 15 min) on Telemetry, instead of ~80.
* A quiet screen (clock) makes about 1 partial a minute and so reaches 30 partials only after 30 minutes: it clears about every 30 minutes. Fine, it ghosts less.
* If the glass shows ghosts, pick 5; if not, 30, 2 hours or never. The default is the number to confirm with the check below.

## Measured on a panel

Real EE05, Telemetry pinned, a value changing every 2.5-4 s, live data, 30 minutes at the default 10 min: one full refresh,
at 902.4 s (the 15 min cap: Telemetry never rests), 3396 partials, no NAKs or reconnects. Change-to-repaint median / p90 / max
790 / 989 / 1831 ms over 553 changes, and none waited behind a full refresh. Per-screen repaint times are in
[partial-refresh.md](partial-refresh.md).

Not measured on the glass: a short setting (`--ghost-min 2`, expected one flash every ~3 min) and 0 (expected none). Both are covered by
unit tests with a fake clock. The 30-minute run has one flash in its window, so it shows the cap, not an interval between two.
To measure intervals: `python3 decky/tools/hw_e2e.py ghost --minutes 30 --ghost-min 10` (and `--minutes 10 --ghost-min 2`, `--minutes 6 --ghost-min 0`).

## Checking ghosting on the glass over an hour

Pin Telemetry (Quick panel) with the default and do not touch it for an hour.

1. At 0, 10, 20, 30, 45, 60 minutes look at the panel from the side, in normal room light: a pale outline of old digits or bars behind the current ones.
   Look at the places that change most: the numbers and the bar graphs.
2. Expect the flash at about 15 minutes (cap), then again about 15 minutes later: two to four per hour.
3. Compare right before and right after a flash. Before: faint ghosting is acceptable if you have to look for it. After: it should be gone.
4. If it is visible before the flash, set **every 5 min**. If you see nothing at 15 min, try **30 min** or **never** and look again the next day.

## Tests

`decky/tests/engine/test_engine_engine.py` (decision table, count gate alone, time gate alone, quiet deferral, cap, screen switch resets, 0 = never, backwards clock,
timer starts at the acknowledged full refresh, reconnect), `test_engine_settings.py` (range, types),
`decky/tests/plugin/test_callables.py` (set_settings), `decky/test/SettingsPage.test.tsx` (the dropdown).
