# EE05 partial refresh: 1.8 s -> 0.67 s

Target was "a changed value repaints in under 1 s". Measured on the real Seeed XIAO EE05
(ESP32-S3) + 5.83" 648x480 UC8179 panel, over the USB protocol, steady state.

| | before (GxEPD2) | after (`fastpartial`, default) |
|---|---|---|
| partial refresh, 64x32 | 1.78-1.80 s | **671 ms** (median of 60, p90 671) |
| partial refresh, 200x96 | 1.80 s | **677 ms** (median of 60, p90 677) |
| full refresh | 4.10 s | 4.02 s |

Spread is 1-2 ms: the time is set by the waveform, not by the data.
Visual quality (ghosting) cannot be read back by the test tools, see "Visual checks".

## Where the 1.8 s went

`decky/tools/pbench.py` drives `firmware/src/pbench/main.cpp` (env `ee05_pbench`), which runs
the same driver as the product firmware with every stage timed. Stock GxEPD2 partial
waveform replayed on it (`set pre 35`, `set drive 35`), 20 refreshes of a 64x32 window:

| stage | ms | note |
|---|---|---|
| reset + register/LUT load | 20 | once per init |
| `0x04` power on (BUSY) | 161 | every refresh in GxEPD2 |
| SPI: window + both RAM planes | 0.6 | 64x32. 200x96: 4 ms. Whole panel: 70 ms |
| `0x12` refresh (BUSY) | **1484** | 73 frames x 20.35 ms |
| `0x02` power off (BUSY) | 41 | every refresh in GxEPD2 |
| total | 1686 | GxEPD2 firmware over USB: 1.78-1.80 s |

The waveform is the cost. `GxEPD2_583_T8` partial LUT is T1=30, T2=5, T3=30, T4=5 frames:
70 frames, plus 3 fixed frames the controller adds to every refresh, at 20.35 ms per frame
(the power-on default of the frame-rate PLL). That is 88% of the time. It is not "two
phases of the controller" and not window-size dependent, which is why 64x32 and 200x96
cost the same. USB (167 ms for a whole frame) and SPI are irrelevant.

What the 70 frames do: for black->white the LUT drives 35 frames toward black (the pixel
already is black, so this changes nothing optically) and then 35 frames toward white. It is
a charge-balance pre-phase. Dropping it halves the time; the drive that actually moves the
particles is the second half.

## How the fast partial works

`firmware/src/ee05/fastpartial.h` (pure C++, host tested) and `uc8179_fast.h` (SPI driver):

- **Single-phase waveform.** LUT 0x22 (K->W) is one phase of VDL for `drive` frames, 0x23
  (W->K) one phase of VDH. WW, KK, VCOM and border LUTs are all zero, so pixels that do not
  change are not driven at all. K->W and W->K drive equally long, so a pixel that goes
  there and back nets zero charge. `Params.pre` re-adds the stock-style pre-phase.
- **Both RAM planes written every time** from our own shadow of the glass (38,880 B):
  `0x10` = what is on the glass, `0x13` = new. Nothing relies on the controller's
  new->old copy (`N2OCP`, datasheet p.28; the driver sets it but never depends on it).
- Register values (`0x01`, `0x00=0x3F`, `0x82=0x00`, `0x50=0x39,0x07`, `0x60`) are each traced to
  the panel datasheet, or marked as our own tuning with its evidence, in `docs/uc8179-tables.md`.
- **Full refresh** uses the controller's OTP waveform (`0x00=0x1F`) with both planes set to
  the image; it is the ghost-clearing path (4.0 s, flashes).
- **Unknown glass.** After the panel drew its own screen, or after boot, the shadow is
  invalid: the first partial of a host session is promoted to a full refresh (measured
  4.02 s), so the old plane is never wrong. Hosts should still send a full first.
- The SPI transaction is taken per call; do not hold it across calls.
- Not used, deliberately: a two-refresh partial (clear window, then draw) and the OTP temperature
  trick (`0xE5`; unmeasured).

Where `drive` comes from: stock main drive was 35 frames (K->W) and 30 (W->K). 20 frames is
407 ms, about 60% of that. Typical E Ink fast "direct update" waveforms are 250-300 ms at
15 V, so 20 leaves margin for a cooler room. This is reasoning, not an optical measurement.

## Numbers

Sweep, bench firmware, `pre 0`, median total ms per refresh (n=20 each; p90 within 1 ms
of median), DC-DC powered off after every refresh:

| drive frames | drive ms | 64x32 | 200x96 |
|---|---|---|---|
| 6 | 122 | 385 | 391 |
| 10 | 204 | 466 | 473 |
| 14 | 285 | 547 | 554 |
| 16 | 326 | 587 | 594 |
| **20 (default)** | **407** | **669** | **675** |
| 30 | 611 | 872 | 879 |
| 35 | 712 | 973 | 980 |
| stock 35+35 | 1425 | 1685 | 1691 |

`refresh = (frames + 3) x 20.35 ms`; total adds 161 + 41 ms of power cycling.
Through the USB protocol (`send_test.py --partials 60`, firmware default 20): 64x32
median 670, p90 671, min 670, max 671; 200x96 median 677, p90 677. Window data does not
matter much: a whole-panel partial is 703 ms at drive 16 (70 ms of SPI, and the
refresh itself takes 47 ms longer than for a small window).

Limits hit:

- 3 frames (61 ms) fixed overhead per refresh, and 161 + 41 ms of DC-DC power cycling.
- DC-DC kept on between refreshes (bench `set keep 1`) removes the 200 ms: 387 ms at drive
  16, 0.47 s at 20. Shipped as a hold window (`Epd::holdMs`, 2 s in `main.cpp`, `Epd::service()` powers off
  after it); the first update after a rest still
  pays the 0.65 s.
- Frame-rate PLL (`0x30`) only rescales the same drive: 0x3C = 9.4 ms/frame, 0x3A = 11.3,
  0x29/0x39 = 12.8 (a 3-frame overhead shrinks too). Not used. Reproduce with bench
  `raw 0x30 0x3c` after a partial.
- Below roughly 10 frames (200 ms of drive) the particles probably do not finish moving.
  The floor is the pigment, not the controller. Unverified here.

## Pixels the window only covers (VCOM_DC)

A partial refresh drives the whole window, not only the pixels that changed: unchanged pixels get the all-zero WW / KK LUT, which is
"source at GND", while VCOM stays at `VCOM_DC` (`0x82`). That is a field of `0 V - VCOM_DC` across every pixel in the window for the
whole refresh. With `VCOM_DC` = -2.0 V (the earlier default) it is +2 V toward black on each white pixel in each window, every
refresh, and it made black->white 27 % weaker than white->black (13 V against 17 V). Windows are merged boxes (a column of values
plus the chart rows between them is one window), so a lot of unchanged white and hatched area is covered, twice a second on Telemetry.
Only a full refresh undid it. The shipped value is `0x00` (-0.1 V). `INK_PARTIAL_VCOM` selects another one for an A/B
(`-D INK_PARTIAL_VCOM=0x26` is the old one). Numbers and the model: [uc8179-tables.md](uc8179-tables.md) section 2,
`decky/tests/test_panel_model.py`.

## Ghosting policy (recommendation)

The host owns it (REFRESH kind 0/1). Start with a **full refresh every 30 partials**, the
interval the BLE firmware used, and always on a page change. A single-phase waveform
leaves more ghost than the stock balanced one, so expect to need this. Loosen to 60-100 if
the glass stays clean; tighten to 15 if outlines of old content remain. Full refresh
costs 4.0 s and flashes the panel, so do it when the user is not watching a value change
(page switch, idle for a minute with 10+ partials queued up). The engine does this: a due full refresh waits for a
quiet render and is forced after a hard cap; the current policy is time-based ([ghost-clear.md](ghost-clear.md), [engine.md](engine.md)). A cold room slows the
particles: raise `drive` (see below) before shortening the interval.

Timing was flat across 60 consecutive worst-case partials (every pixel of the box
inverts each time): 670-671 ms. Timing says nothing about ghosting.

## Visual checks

Nothing in the test tools can read the glass back. To check on a real panel:

1. `python3 decky/tools/send_test.py --partials 60 --keep`: after 60 inversions of the
   64x32 and 200x96 boxes, is there a faint outline / gray cast where the boxes were?
2. Are black and white fully saturated right after a single partial (contrast)?
3. After the host session ends, does the panel's own screen come back clean?
4. Same checks in a cold room (waveform timing is not temperature compensated).

For a busy screen: `python3 decky/tools/ink_cycles.py --seconds 300 --keep` (Telemetry, values and charts change every second, partial
refreshes only, the last frame stays on the glass) or `--screens telemetry,thermals-power --switch-s 30` for screen switches. Flash a
build with `-D INK_PARTIAL_VCOM=0x26` (the old value) and one without, run it on each, and compare the white and hatched areas next to
the updating values.

Tune without reflashing the product firmware: flash `ee05_pbench` and
`python3 decky/tools/pbench.py "set drive 28" "run 100 100 200 96 20 0 0"` (or
`... 8 8 64 32 20 1 0` for random text-like churn). Set the winner with
`-D INK_PARTIAL_DRIVE=<frames>` in `build_flags`, or edit `FAST` in `fastpartial.h`.
`set pre N` adds the stock-style balance phase (+20 ms per frame).

## Tests and commands

- `python3 -m pytest decky/tests` (72 passed). `firmware/host_test/fastpartial_host.cpp`
  (run by `decky/tests/test_fastpartial_host.py`) checks the LUT bytes, window snapping
  (including odd x/w and the 81-byte row stride), the timing model against the measured
  stock 1484 ms, and the percentile code.
- `pio run -e ee05` and `-e ee05_pbench` build. Hardware: `send_test.py --partials N`,
  `edge_test.py --slow` (all OK with the new driver, including the 16 s host-timeout
  handback to GxEPD2), `pbench.py`.
- Not covered by automated tests: the SPI driver itself (hardware only) and the picture.

To restore the original firmware, use the backup/`write_flash` command in
`docs/usb-protocol-v1.md` ("Hardware test procedure").
