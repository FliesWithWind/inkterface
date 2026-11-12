# Idle mode

While the host is alive the panel is a dumb framebuffer (`docs/usb-protocol-v1.md`). When the
host goes quiet (machine off or asleep, USB port still powered) the panel draws its own
screens from a **declarative layout stored in flash**, pulling data over Wi-Fi. The host
only compiles and uploads layouts; the panel renders them with no host present.

Code map

| what | where |
|---|---|
| layout wire format + decoder, field tables | `firmware/src/ee05/idle_layout.{h,cpp}` |
| store format, uploader, flash slots | `firmware/src/ee05/idle_store.{h,cpp}` |
| mode state machine | `firmware/src/ee05/idle_mode.{h,cpp}` |
| 2-bit canvas, fonts, icons | `firmware/src/ee05/idle_canvas.{h,cpp}`, `idle_fonts.{h,cpp}`, `idle_fonts_data.cpp` (generated) |
| layout interpreter | `firmware/src/ee05/idle_render.{h,cpp}` |
| data sources (weather, Steam, http_json, clock) | `firmware/src/ee05/idle_sources.{h,cpp}` |
| host compiler, store packer, uploader | `decky/py_modules/inkterface_core/idle/` |
| sleep hint (logind -> `MODE PANEL`/`MODE HOST`), stdlib D-Bus client | `decky/py_modules/inkterface_core/sleep_watch.py`, `dbus_min.py`, `Engine.prepare_sleep()/wake()` |
| bus-suspend fallback (no SOF -> idle) | `firmware/src/ee05/usb_bus_watch.h`, `idlemode::Controller::onBusSuspend`, `main.cpp busPoll()` |
| built-in idle packages | `decky/screens/{clock-weather,steam-stats,minimal-clock}/screen.json` |
| tests | `firmware/host_test/idle_*_host.cpp`, `firmware/host_test/usb_bus_watch_host.cpp`, `decky/tests/idle/`, `decky/tests/test_sleep_idle.py` |

## Layout wire format (`ILY1`)

All integers little endian. A layout is `header | sources | widgets | pool | crc32`, at most
**8192 bytes** (`MAX_LAYOUT_BYTES`). The compiler produces it from the `idle` section of a
`screen.json`; the firmware decoder re-validates everything.

```
header (16)
  0  magic        "ILY1" (49 4C 59 31)
  4  version      u8 = 1
  5  render_mode  u8: 0 mono, 1 gray4
  6  refresh_s    u16, 1..65535: mono = repaint tick, gray4 = full refresh period
  8  full_every   u16, 1..65535: mono = partial refreshes between full ones (ghost clear)
 10  flags        u8, bit0 = dark (black background, white default ink); other bits 0
 11  n_sources    u8, 0..8
 12  n_widgets    u8, 0..64
 13  reserved     u8 = 0
 14  pool_len     u16
sources  n_sources x 8
widgets  n_widgets x 28
pool     pool_len bytes. pool[0] = 0 (offset 0 is the empty string)
crc32    u32, IEEE (zlib.crc32) over every preceding byte
```

Total length must equal `16 + 8*n_sources + 28*n_widgets + pool_len + 4` exactly.

**Strings** are NUL-terminated byte strings in the pool, addressed by `u16` offset;
`0xFFFF` means absent. A referenced string must be fully inside the pool with its NUL
inside the pool. Bytes are printable ASCII `0x20..0x7E`, plus `0x7F` which renders the
degree sign, plus template tokens (below). No other control bytes.

**Source** (8 bytes): `type u8 | flags u8 | poll_s u16 | s1 u16 | s2 u16`

| type | name | flags | s1 | s2 |
|---|---|---|---|---|
| 1 | clock | 0 | POSIX TZ override, or absent = global zone | absent |
| 2 | weather | bits0-1 units: 0 global, 1 metric, 2 imperial | `"lat,lon"` override, or absent | absent |
| 3 | steam | 0 | decimal appid for `players`, or absent = configured default | absent |
| 4 | http_json | 0 | `https://` URL, 9..255 chars (required) | JSON pointer (RFC 6901) or `""` = whole document, <= 128 chars |

`poll_s` 0 = default (weather 900, steam 900, http_json 300; ignored for clock), otherwise
>= 30. Unknown type, unknown flag bits, or a `s1`/`s2` that the type does not use are
rejected.

**Widget** (28 bytes):

```
 0 type u8   1 text, 2 number, 3 bar, 4 icon, 5 image, 6 clock
 1 flags u8  bit0 invert, bit1 clip (text: no "..."), bit2 wrap, bit3 img_2bpp
 2 x u16   4 y u16   6 w u16   8 h u16      box, 1 <= w,h; x+w <= 648; y+h <= 480
10 font u8   id, see Fonts
11 align u8  bits0-1 halign (0 left 1 center 2 right), bits2-3 valign (0 top 1 middle 2 bottom)
12 color u8  0 black, 1 dark gray, 2 light gray, 3 white
13 border u8 0..4 px frame inside the box
14 text u16  pool offset: text template / clock strftime format
16 aux u16   pool offset: number unit / image bitmap bytes
18 src u8    source index or 0xFF
19 field u8  field id (see Fields)
20 idx u8    array index for indexed fields
21 p0 u8     number: decimals 0..6 or 0x7F (default); bar: 0 horizontal, 1 vertical;
             icon: 0 weather set, 1 named set
22 p1 u8     icon named set: icon id (0 sun 1 cloud 2 rain 3 snow 4 storm 5 fog 6 clock 7 gamepad)
23 max_lines u8  text wrap: cap, 0 = box height
24 lo i16   26 hi i16     bar range (lo < hi)
```

Per type: `text` needs `text`; `number` needs src+field of kind N/A; `bar` src+field N/A and
`lo<hi`; `icon` weather set needs src+field N (a WMO code), named set needs `p1 < 8`;
`image` needs `aux` with `ceil(w/8)*h` bytes (`ceil(w/4)*h` for 2bpp; 1 = black in 1bpp,
2-bit value 0 black .. 3 white) fully inside the pool and the flag matching the layout
mode (2bpp only in gray4); `clock` needs `text` (format) and src+field of kind T. Fields the
type does not use must be zero/absent. `color` 1 or 2 on text, number and clock widgets is
rejected in mono layouts (gray text dithers to mush); fills may use them, a mono panel
dithers them.

**Text templates.** Literal bytes plus 5-byte tokens `01 | src+1 | field+1 | idx+1 | spec`.
`spec` is the ASCII digit `'0'..'6'` (decimals) or `'D'` (default format: up to 1 decimal,
a trailing `.0` dropped; the only valid spec for S and T fields). The four bytes after `01`
are exempt from the "printable only" rule, and nothing else may contain a `01` byte. A token
must reference an existing source/field/idx of kind N, S or A (T is only for clock
widgets). The compiler turns `{wx.forecast[0].max:.0f}` into a token; `{{` and `}}` are
literal braces.

**Clock formats** (`strftime` subset, English names, evaluated by the panel): `%H %M %S %I
%p %A %a %d %e %B %b %m %y %Y %j %%`. Anything else is rejected.

### Fonts

Inkterface Mono (IBM Plex Mono without hinting instructions; the host emulator's font). Pixel size = id table; width = 0.6 x size.

| ids | style | sizes | charset |
|---|---|---|---|
| 0..5 | regular | 14 18 22 28 36 48 | ASCII 32..126 + degree |
| 8..13 | bold | 14 18 22 28 36 48 | ASCII 32..126 + degree |
| 16..18 | bold | 80 120 160 | `0123456789:.-/% ` + degree |

A string using a character the font lacks is rejected at compile time and decode time.
`screen.json` spells fonts as `regular-28`, `bold-160`.

### Fields (what a widget or token binds to)

Kinds: N number, S string, T time (epoch seconds), A number or string. `idx` valid range
in the last column (blank = not indexed, idx 0).

| source | id | `screen.json` path | kind | idx |
|---|---|---|---|---|
| clock | 0 | `now` | T | |
| weather | 0 | `current.temp` | N | |
| weather | 1 | `current.code` (WMO) | N | |
| weather | 2 | `current.condition` | S | |
| weather | 3 | `today.max` | N | |
| weather | 4 | `today.min` | N | |
| weather | 5 | `today.sunrise` (`HH:MM`) | S | |
| weather | 6 | `today.sunset` | S | |
| weather | 7 | `today.rain` (%) | N | |
| weather | 8 | `forecast.day` (`MON`..) | S | 0..2 |
| weather | 9 | `forecast.max` | N | 0..2 |
| weather | 10 | `forecast.min` | N | 0..2 |
| weather | 11 | `forecast.code` | N | 0..2 |
| weather | 12 | `forecast.condition` | S | 0..2 |
| weather | 13 | `forecast.rain` | N | 0..2 |
| steam | 0 | `players` | N | |
| steam | 1 | `online` | N | |
| steam | 2 | `persona` | S | |
| steam | 3 | `level` | N | |
| steam | 4 | `games_owned` | N | |
| steam | 5 | `hours_total` | N | |
| steam | 6 | `friends_online` | N | |
| steam | 7 | `friends_ingame` | N | |
| steam | 8 | `recent.name` | S | 0..2 |
| steam | 9 | `recent.hours2w` | N | 0..2 |
| steam | 10 | `achievement.name` | S | |
| steam | 11 | `achievement.unlocked` | N | |
| steam | 12 | `achievement.total` | N | |
| http_json | 0 | `value` | A | |

`forecast[0]` is tomorrow. In `screen.json` a binding is `<source name>.<path>[n]`, e.g.
`wx.forecast[1].max`. The compiler resolves names to indices; the panel never sees names.
`firmware/host_test/idle_layout_host --dump-fields` prints the firmware table; a pytest
compares it with the compiler's.

## Store format (`ISTR`), what the host uploads

One blob holds **all** layouts, the rotation, the active selection and the panel config, so
an upload is atomic.

```
 0  magic          "ISTR"
 4  version        u8 = 1
 5  n_layouts      u8, 1..8
 6  active         u8, index of the shown (rotate off) or first (rotate on) layout
 7  flags          u8, bit0 = rotate through all layouts
 8  rotate_s       u16, seconds per layout when rotating, 10..65535 (ignored if not rotating)
10  config_len     u16
12  config         config_len bytes of TLV entries: tag u8 | len u8 | value
    layouts        n_layouts x ( len u16 | ILY1 layout, len bytes )
    crc32          u32 over every preceding byte
```

At most **49152 bytes** (`MAX_STORE_BYTES`). Config tags (unknown tag, duplicate tag,
or an over-long value rejects the store): 1 wifi_ssid (<=32), 2 wifi_pass (<=63), 3 tz POSIX
(<=63), 4 lat text (<=15), 5 lon text (<=15), 6 units u8 (0 metric, 1 imperial), 7
steam_key (<=40), 8 steam_id (<=20), 9 steam_appid u32. Text values carry no NUL.
Every layout must pass `decodeLayout`, otherwise the whole store is refused.

## Transport

`IDLE_LAYOUT` (0x20), host to panel, payload `total u32 | offset u32 | data`. `total` is the
store length (<= 49152). A chunk with `offset == 0` starts an upload and drops any partial
one. Chunks arrive in order (`offset` equal to the bytes received so far); a repeat of an
already-received chunk is ACKed without effect (host retry after a lost ACK); a gap or
`total` change is NAKed. `offset + len > total` is NAK BAD_LEN. Each chunk is ACKed; the
final chunk's ACK means *validated and durably written*. A store that fails validation is
NAK `BAD_DATA` (14); a flash write failure is NAK `STORAGE` (15). (7..13 are OTA's.) Either leaves the previously
stored layouts in force. Chunk size up to 4088 bytes; allow ~3 s for the final ACK.

`MODE` (0x12) is now `mode u8 [| idle_timeout_s u16 [| layout u8]]`. `mode`: 0 HOST, 1
PANEL (show idle now, and stay there). `idle_timeout_s` 0 keeps the current value,
otherwise 15..3600 (the host heartbeats every 5 s, so anything shorter than 15 s would
trip on one late tick). Default 60 s if never set; a non-zero value is **persisted** in
NVS (`inktf/idle_to`) and survives reboot. `layout` 0xFF keeps the selection, otherwise an index into the stored
layouts (not persisted; a reboot returns to the stored `active`). Anything else is NAK
BAD_LEN/BAD_ARG. HELLO reports `caps` bit0 gray4 and bit1 idle_storage, and
`idle_flash_free` = the store capacity (49152).

## Host side in the Decky plugin

`inkterface_core/idle/plan.py` compiles the screens the user selected (`host.json` `idle_screens`, in order, each in its chosen render mode),
packs them with the rotation and the CONFIG TLVs (POSIX tz from the host's zone file, lat/lon text, units, Steam key and id, Wi-Fi SSID and
password) and the engine uploads the result on connect and on change (docs/engine.md "Idle store upload"). `MODE HOST` carries the user's
`idle_timeout_s` (15..3600). Nothing is uploaded when the firmware lacks `idle_storage`, when a layout is over 8192 bytes, when the store is
over 49152 bytes (or the panel's reported capacity) or when a screen does not compile; the Standby Screens page shows the reason. NAKs: `BAD_DATA`
14, `STORAGE` 15 (7..13 are OTA's; `decky/tests/test_proto_numbering.py` and `static_assert`s keep Python and C++ in step).

## State machine

`firmware/src/ee05/idle_mode.h` (pure, host tested). Three states own the glass:

```
            MODE HOST                      silence >= idle_timeout_s
  WAIT ----------------> HOST ------------------------------------------> IDLE (auto)
   |  \                   ^ |                                                |
   |   \ silence from boot| | MODE PANEL                                     | any valid frame
   |    +-----------------|-|---------------------> IDLE (auto) <------------+  -> back to WAIT or HOST,
   |                      | v                                                      whichever it came from
   +-- MODE PANEL --> IDLE (pinned) --- MODE HOST ---> HOST        (frames and heartbeats do NOT wake a pinned idle)
```

- **WAIT** is the state after boot: nobody owns the glass (it keeps showing whatever it showed). Valid
  frames reset the silence clock but RECT/REFRESH are refused (`WRONG_MODE`) until `MODE HOST`.
- **Bus quiet** (new): a host that suspends keeps VBUS but stops the USB bus (no SOF packets). The firmware
  enters auto idle ~2 s after the bus went quiet instead of after the silence timeout; see "Sleep and wake".
- **Silence** is measured from the last CRC-valid frame of any type, against `idle_timeout_s`: default
  60 s, range 15..3600 s, set by `MODE`, persisted in NVS key `inktf/idle_to`. The Decky engine
  heartbeats every 5 s, so 15 s tolerates two late ticks.
- **Wake**: the first valid frame leaves an auto idle. Wi-Fi is switched off, the idle buffers are freed,
  and the display handle goes back to the host. The host's shadow framebuffer was never touched by idle,
  and the driver is invalidated, so the host's next `REFRESH` (even a partial one) is promoted to a
  full refresh that repaints the whole host picture. Worst-case wake latency: whatever blocking
  operation is in flight: an e-paper refresh (<= 4.1 s full, 0.7 s partial) or a Wi-Fi connect
  attempt (non-blocking) or an HTTP request (aborted at the next byte or 1 ms poll when a host byte
  arrives; connect phase <= 5 s). Frames are queued in the 16 KiB USB RX buffer meanwhile.
- **Pinned idle** (`MODE PANEL`, what the engine sends on `stop()`): idle now, stays idle until `MODE HOST`.
- Boot with no host: idle after `idle_timeout_s` from boot.
- Flash writes happen only when a store is uploaded (and NVS only when the timeout changes), never per
  refresh or per minute, so idle mode does not wear the flash.

## Sleep and wake

The Steam Machine suspends itself (s2idle) with the USB port still powered. Nothing runs on the host during a suspend, so the
silence timeout alone would only fire *after* the resume. Two layers make the panel follow the machine:

1. **Host hint (primary).** `SleepWatcher` (a thread in the plugin) listens to systemd-logind on the system bus and holds a
   *delay* inhibitor lock (`Inhibit("sleep:shutdown", ..., "delay")`).
   * `PrepareForSleep(true)` / `PrepareForShutdown(true)`: `Engine.prepare_sleep()` sends `MODE PANEL` (pinned idle, the existing
     "idle now" value: no new protocol number) and waits for the ACK (<= 3 s), then the lock is released and the suspend
     proceeds. The panel ACKs before it draws, so this takes ~20 ms; the idle screen is on the glass ~4.1 s later (one full
     e-paper refresh, which the panel finishes on its own while the host sleeps).
   * While asleep the engine sends nothing (no heartbeat, no frame, no reconnect: a heartbeat would not wake a pinned idle, but
     a reconnect's `MODE HOST` would) and only drains the panel's replies.
   * `PrepareForSleep(false)` (also an aborted suspend/shutdown): the lock is taken again and `Engine.wake()` sends `MODE HOST`
     and redraws everything with a full refresh (the glass shows the idle picture). If the port went away during the suspend the
     engine reconnects at once instead of after the 2 s backoff.
   * Decky `_unload` / Steam shutdown: `Engine.stop()` sends `MODE PANEL` as before and now waits (<= 0.5 s) for its ACK before
     it closes the port; the hint thread is stopped first, which releases the lock.
   * **The lock never delays a suspend by more than 4 s** (logind's own cap is `InhibitDelayMaxUSec`, 5 s on SteamOS): it is
     released after the engine answered, when the engine call raises, when it is still stuck after `SLEEP_BUDGET_S` (4 s), when the
     panel is unplugged (no ACK is awaited without a session) and when the bus connection dies. If the lock cannot be taken
     (polkit) the hint still works, it just cannot delay the suspend.
   * **Lost resume signal:** an engine that has been "asleep" for 20 s on its own clock (frozen during a real suspend, so this only
     happens if the suspend never took place) wakes itself.
   * D-Bus: `dbus_min.py`, stdlib only (Decky's Python has no dbus bindings): `AF_UNIX` to `/run/dbus/system_bus_socket`,
     `AUTH EXTERNAL` with the uid, `NEGOTIATE_UNIX_FD` (the lock is a file descriptor passed with `SCM_RIGHTS`), `Hello`, `AddMatch`,
     `GetNameOwner(login1)`. Signals are believed only from logind's unique name. Malformed messages are skipped, a garbled stream
     or a closed socket reconnects with a 1..30 s backoff (the lock is released while disconnected).
2. **Firmware fallback (if the hint is lost: plugin not running, D-Bus down, lock refused).** The Arduino core already turns the
   USB-Serial-JTAG SOF packets into `HWCDC::isPlugged()` (a FreeRTOS tick hook; it owns and clears the SOF interrupt bit, so the
   firmware reads that instead of the register). `usb_bus_watch.h` fires once when it has been false for `INK_BUS_DOWN_MS` (2000 ms)
   of consecutive loop samples; `Device::onBusSuspend()` then enters auto idle and ends the host session. The first valid frame
   after the resume brings the host back, and because the session ended the panel announces itself with an unsolicited HELLO,
   which the engine treats as "panel lost my session": `MODE HOST` plus a full redraw. It does **not** fire:
   * on a flapping bus or a bus reset: the outage must last 2 s without a single SOF sample (measured: a `USBDEVFS_RESET`
     re-enumeration is a few ms);
   * across a blocked loop (e-paper refresh up to 4.1 s, Wi-Fi connect): a gap of more than 250 ms between two samples restarts
     the outage clock, so blocked time never counts;
   * while an OTA transfer is open (`Receiver::busy()`: BEGIN..END with a frame in the last 10 s, or committed and about to
     reboot; an abandoned, resumable session stops counting after 10 s);
   * while already idle (nothing to detect).
   Boot without a USB host at all (wall adapter) now reaches idle after ~2 s instead of 60 s.

Timing on a dev panel (EE05, host = real engine + real watcher + fake logind bus,
`decky/tools/sleep_test.py hint`, 5 rounds, host monotonic clock): signal -> MODE ACK / lock released **20 ms**; signal -> idle
screen drawn **4.09 s** (the full refresh is 3995 ms); resume signal -> active screen on the glass **4.25 s** (MODE HOST, 10 bands,
full refresh). Without the hint, the active frame stays up for the 60 s silence timeout *after* the resume.
Fallback, default firmware: bus stops -> idle entered after 2.0 s (+ 4.0 s refresh = idle drawn ~6 s); resume -> first heartbeat
(<= 5 s) -> panel leaves idle and sends HELLO -> full redraw (4.2 s).

Not verified on the Steam Machine (it cannot be suspended remotely): that s2idle really stops the SOF packets on its port, and the
end-to-end suspend. The check below covers both.

### Check on the Steam Machine (1 minute)

1. Plugin installed and the panel showing an active screen. In a terminal: `journalctl -f | grep -i "sleep hint"` (or watch the
   Decky plugin log), then `systemd-inhibit --list | grep -i Inkterface`: one `sleep:shutdown ... delay` lock from the plugin.
2. Suspend from the Steam power menu. Expect: the log shows `sleep hint: PrepareForSleep(true): panel to idle`, the panel starts its
   idle screen within a second (the glass finishes ~4 s later), the machine suspends normally (no extra delay).
3. Wake the machine. Expect: `sleep hint: resumed`, the active screen is back within ~5 s.
4. Fallback: stop the plugin (Decky: disable it) and suspend again. Expect the idle screen about 2 s after the bus went quiet (on the
   glass ~6 s later, the refresh takes 4 s) and, after the resume, nothing until the plugin runs again. Re-enable the plugin: the
   active screen is back after its reconnect. If the panel still waits ~60 s, this machine keeps sending SOF in suspend (or cuts VBUS):
   send the panel's debug lines (`usb: no SOF ...`, `usb: SOF gap ...`, readable with `decky/tools/send_test.py --log` or the
   plugin log while connected).

## Storage

Partition `idlestore` (raw data, subtype 0x40, `0x670000`, 128 KiB) was added to `firmware/partitions.csv`
after `app1`; `nvs`, `otadata`, `app0`, `app1` are untouched, so a flash that rewrites the partition
table keeps the old NVS (Wi-Fi, legacy config). Two slots of 64 KiB: sector 0 holds a 20-byte record
(`"ISLT"`, seq, length, blob CRC32, header CRC32), sectors 1.. the store blob. A commit erases the slot
that is **not** the newest valid one, writes the blob, then writes the header last, then reads it all
back and re-validates it (CRC + full `parseStore`). The newest slot whose record, CRC and content
validate wins at boot; if neither validates the panel shows the **built-in fallback layout** (compiled
into the firmware from `decky/screens/minimal-clock`, `idle_fallback.h`). Host tests cut the power at
every single erase/write of a commit and assert the previous store always survives (and the next
upload still works).

## Layout set, rotation, fallback

`idle_layoutset.{h,cpp}`: the stored layouts in order, the `active` index and, with `rotate`, the
`rotate_s` period (counted from entering idle). MODE `layout` selects an index until the next reboot.
A rotation or selection change reconfigures the sources (one source set per layout) and forces a full
refresh. A store that fails validation never replaces a working one (the whole upload is refused).

## Rendering

- **Canvas** (`idle_canvas`): 2 bits per pixel, 648x480 (77,760 B) allocated only while idle.
  Level 0 black .. 3 white. Mono output thresholds 0/3 and dithers levels 1 and 2 with a Bayer 4x4
  matrix (fills only: gray text is rejected at compile time in mono layouts).
- **Fonts** (`idle_fonts_data.cpp`, generated by `firmware/scripts/gen_idle_fonts.py` from Inkterface Mono = IBM Plex Mono
  without hinting instructions, OFL-1.1): 1-bit auto-hinted glyph bitmaps (one stem width per size), 93 KB of flash.
- **Interpreter** (`idle_render`): widgets are painted in order, each clipped to its own box. Text that
  does not fit gets "..." (or is clipped with the `clip` flag); wrapping is word based with a hard split
  for over-long words. Missing data draws `--` (a number with no value is just `--`), an empty bar frame,
  a short dash instead of an icon, `--:--` for a clock that is not synced. Nothing in the renderer
  allocates per widget or can run out of bounds on a validated layout; `renderLayout` also tolerates
  hostile values (NaN, huge numbers, unknown weather codes).
- **Weather icons** are drawn from circles/lines on a 64-unit grid scaled to the widget (WMO code ->
  sun / partly cloudy / cloud / fog / rain / snow / storm); named icons: sun cloud rain snow storm fog
  clock gamepad.
- **Painter** (`idle_painter`): decides how a rendered frame reaches the glass.
  - `mono`: first paint (and after any host drawing) is a full refresh (OTP waveform, ~4 s). After that
    each paint diffs the new 1-bit picture against what the driver knows is on the glass and sends only
    the byte-aligned bounding box as a fast partial refresh (0.67 s), or nothing if identical. After
    `full_refresh_every` partials (default 60) the next paint is a full refresh to clear ghosting.
    Repaint tick: `refresh_interval_s` (default 60; with 60 s it aligns to the minute so a clock never
    lags more than a second) or as soon as fetched data changed.
  - `gray4`: always a full-screen 4-level refresh (the static gray waveform of `LUT_GRAY`, see `docs/uc8179-tables.md` section 6); skipped
    if the picture is byte-identical (CRC of the canvas) to the last gray one. Repaint every
    `refresh_interval_s` (default 900). The first paint waits for the first fetch round (or Wi-Fi
    failure, or 30 s) so the 4 s flash is not spent on placeholders. Gray frames leave the glass in a
    state mono partials must not build on, so the next mono paint is a full refresh.

## Sources and Wi-Fi

- Wi-Fi is **on only while idle**. It connects without blocking the loop (USB stays serviced), gives up
  after 20 s and retries every 60 s. Empty SSID = no attempt. Credentials: `idle_config.h` defaults,
  then the legacy NVS blob `inktf/idlecfg` (read-only migration of what the BLE app provisioned), then
  the store CONFIG section pushed over USB (highest priority).
- **NTP**: `configTzTime(tz, "pool.ntp.org", "time.nist.gov")` once per connection; the POSIX TZ comes from
  the config (store tag 3, host timezone). The RTC keeps counting while Wi-Fi is off, so after the first
  sync the clock is correct the moment idle starts. A clock source can override the zone (`tz`).
- `idle_sources.{h,cpp}` (pure C++ + ArduinoJson, tested against a real local HTTP server):
  - **weather**: Open-Meteo, `lat,lon` from config or per-source override, metric/imperial, 4 forecast
    days (today + 3). Poll 900 s.
  - **steam**: keyless `players` (GetNumberOfCurrentPlayers, per-source appid) and `online`
    (store.steampowered.com/stats/userdata.json). With key + SteamID: persona, level, games owned, total
    hours (GetOwnedGames, filtered), 3 recent games with 2-week hours, friends online / in-game (batches
    of 50, up to 400 friends), latest achievement and progress. Poll 900 s. Hours are hours, not minutes.
  - **http_json**: `https://` URL + RFC 6901 pointer to one scalar (number, string, bool); 32 KB body cap,
    8 s timeout, string truncated to 63 bytes. No filter (arbitrary pointers cannot be expressed as an
    ArduinoJson filter); the cap bounds memory instead.
  - A failed fetch keeps the last good values (they are shown until the next success) and retries after
    min(poll, 60 s). One failing endpoint only affects its own fields.
- The `Http` implementation on the panel streams the body through a guard that enforces the byte cap, the
  deadline and an abort hook that fires when a host byte arrives.

## Security and known limits

- **Secrets are stored in plain text**: Wi-Fi password and Steam key sit in the `idlestore` flash
  partition (and, for the legacy path, in NVS). ESP32 flash/NVS encryption is not enabled in this build
  (it was not before either), so anyone who can read the chip can read them. The firmware never logs
  them, and the host test asserts the key appears in no output of the sources. Enable flash encryption
  if the board leaves your control.
- **TLS does not validate certificates** (`setInsecure()`, as the old idle code did): a hostile network
  can read the Steam key, which the Steam Web API takes as a query parameter, and can spoof displayed
  data. Pin the CA bundle to fix.
- The store is validated end to end (CRC, every length, every layout) before it is written; a corrupt
  upload cannot brick the panel, the worst case is the built-in clock.
- Removed with BLE: the BLE GATT service, the BLE-driven dashboard, in-game panel, battery status line,
  the Wi-Fi web OTA page (`inktf.local`), mDNS and remote HTTP OTA. They all needed Wi-Fi/BLE to be on
  permanently; OTA returns as signed USB OTA (`OTA_*` frames are still reserved). The Feather boards'
  BLE firmware is not part of this repo (see the Origin section of the readme).

## Tests

```bash
python3 -m pytest decky/tests -q          # everything, including the g++-built C++ host tests
python3 -m pytest decky/tests/idle -q     # idle only
```

| what | where |
|---|---|
| Compiler: every widget, every rejection, 8192/8193 size cap, version, determinism, pool dedupe | `decky/tests/idle/test_compile.py`, `test_reject.py`, `test_fields.py` |
| Python decoder oracle + byte patches + fuzz | `test_decode.py` |
| **C++ decoder vs Python oracle**: same field table, 3000 CRC-fixed random mutations, truncation at every offset, header bytes, 1500 store mutations | `test_cpp_decode.py` |
| Store pack/parse, upload against a fake link (NAK, retry, final timeout) | `test_store.py`, `test_upload.py` |
| State machine, MODE (timeout 15..3600, persistence hook, layout select, refusals change nothing), IDLE_LAYOUT framing, timeline HOST -> idle -> wake, heartbeat tolerance, rollover, store rules, flash with power loss at every write, uploader, layout set/rotation, painter cadence | `firmware/host_test/idle_mode_host.cpp` via `test_idle_host.py` |
| Renderer widgets (alignment, clip, ellipsis, wrap, number, bar, invert, border, icons, image, clock, gray, dark, placeholders) | `test_render_widgets.py` |
| **Pixel-exact goldens** of the built-ins, rendered by the real firmware renderer | `decky/tests/idle/goldens/*.png`, `test_goldens.py` |
| Sources against a real local HTTP server (110 checks) | `firmware/host_test/idle_sources_host.cpp` via `test_sources_host.py` |
| USB protocol incl. LOG retention, emulator vs C++ byte for byte (MODE extension included) | `usbproto_host.cpp`, `test_cpp_diff.py` |
| Generated fonts / fallback layout are fresh | `test_idle_host.py` |

Update goldens after an intended visual change: `INKTERFACE_UPDATE_GOLDENS=1 python3 -m pytest
decky/tests/idle/test_goldens.py` and look at the PNGs.

## Built-in idle packages

`decky/screens/{clock-weather, clock-weather-gray4, steam-stats, minimal-clock}/` (`screen.json` + the
`sample-data.json` the goldens use). Clock & Weather ships as the default `mono` package and a `gray4`
variant of the same layout (secondary text and rules in gray). Goldens:
`decky/tests/idle/goldens/{clock-weather,clock-weather-gray4,steam-stats,minimal-clock}.png`.
Compile one: `python3 -m inkterface_core.idle.compile decky/screens/clock-weather -o out.bin` (from
`decky/py_modules`), upload with `inkterface_core.idle.upload.upload_store`.

## Size and memory

`pio run -e ee05` (PlatformIO, Arduino-ESP32, the example `idle_config.h`): RAM (static) 132,564 B (40.5 % of 327,680), flash (app)
1,068,909 B (32.0 % of 3,342,336); the idle fonts alone are ~93 KB of flash. Runtime memory on the panel (from the `idle: enter` /
`idle: leave` LOG lines, Wi-Fi failing): internal
heap free ~223 KB when idle starts, ~201 KB at the end, lowest ever ~200 KB, so idle mode costs about 22 KB of
internal heap; the canvas (77.8 KB), the 38.9 KB paint buffer and the source set land in the board's 8 MB
PSRAM (free PSRAM drops 8,369,811 -> 8,207,495 B, ~162 KB, while idle and is returned on leave). A real TLS
session adds its handshake buffers on top (not measurable here). The loop task stack is 16 KiB
for TLS and the Steam fetchers.

## Measured on hardware (EE05 on a dev PC, example Wi-Fi credentials)

`python3 decky/tools/idle_test.py` (uploads each built-in package, silence timeout 15 s, then wakes it):

| step | result |
|---|---|
| HELLO | caps 0x3 (gray4 + idle_storage), `idle_flash_free` 49152; answered in < 1 ms |
| store upload (109 B .. 1,734 B) | 0.10 - 0.12 s including the flash commit |
| silence -> idle | panel entered idle, drew the layout, left on the first frame (all four packages) |
| first idle paint, mono | **full refresh 3,996 ms** (4,016 ms after a reboot) |
| idle minute tick, mono | **partial refresh 688 - 697 ms** (clock digits only) |
| first idle paint, gray4 | **gray4 refresh 2,459 ms** (waveform 2,133 ms) |
| first host REFRESH after idle | promoted to a full refresh, 4,015 ms |
| wake latency | the frame after the silence is ACKed in < 1 ms when the panel is not mid-refresh; a frame that arrives during a refresh waits up to ~4.1 s (use 5 s timeouts while idle) |
| Wi-Fi with the example credentials | `wifi: connecting`, `wifi: connect failed` after 20 s, layouts drawn with placeholders (`--:--`, `--`), no crash, retry every 60 s |
| corrupt upload | NAK `BAD_DATA`, previous store untouched, layout 0 still selectable |
| rotation store (3 layouts) | accepted, layouts 0..2 selectable, layout 3 refused |
| reboot | store (seq N, 3 layouts) loaded from flash; persisted 30 s timeout made the panel idle 30 s after boot (default would have been 60 s) |

For reference, `send_test.py` on the same firmware: full refresh 4.0 s, partial 0.67 s, ~234 KB/s.

## Not yet verified

- **Real Wi-Fi**: the tests above could not fetch real data. The weather / Steam / http_json code is tested against
  a local HTTP server and the real `HTTPClient` path was exercised only up to the connection failure.
  Push credentials with the store CONFIG section,
  then watch one idle cycle: `wifi: connected`, the clock syncing (`--:--` -> time), weather and Steam
  values replacing the dashes, and the `Steam Stats` fields (needs `steam_key` + `steam_id`, public profile).
- **The picture on the glass**: goldens prove the pixels the firmware computes (1-bit and 4-level), not how
  the panel shows them. Look at all four layouts, in particular `clock-weather-gray4`.
- **Ghosting** after many idle partials (`full_refresh_every` default 60 = one hour for the clock layouts)
  and the dither of gray fills in mono mode.
- **Steam Machine behaviour**: wake on resume from sleep (does the USB port stay powered?), the real
  host engine's `stop()` -> `MODE PANEL` -> idle, and a restart -> `MODE HOST` handoff
  (idle on the Steam Machine itself was tested).
- Long soak (hours of idle partials, many sleep/wake cycles) and the Steam friends list with > 400 friends
  (only the first 400 are counted).
