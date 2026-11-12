# Inkterface USB protocol v1

Host renders the screen, the panel blits 1-bit dirty rectangles and refreshes. All host
traffic is framed binary on the panel's USB CDC serial port. Verified on a
real EE05 (see "Measured on hardware / tested").

Code: panel `firmware/src/ee05/usbproto.{h,cpp}` (+ glue in `main.cpp`), host
`decky/py_modules/inkterface_core/{proto,diff,emulator,usb_link,serialport}.py`, tools
`decky/tools/{send_test,edge_test}.py`, tests `decky/tests/` and
`firmware/host_test/usbproto_host.cpp`.

## Transport facts that shaped the design

- The EE05 (XIAO ESP32-S3) enumerates as the chip's built-in **USB-Serial-JTAG**
  (VID `303a`, PID `1001`), not TinyUSB. Full speed, 64-byte bulk endpoints.
- **No back-pressure.** The Arduino `HWCDC` ISR copies each 64-byte packet into a RX
  queue and silently drops what does not fit. Measured: a 40 KB burst into an 8 KB queue
  lost ~8 KB per frame. So the host must window its writes (see "Flow control").
- **Host connect is invisible to the panel.** `if (Serial)` means "USB host enumerated",
  not "a program opened the port". A HELLO sent on attach can be lost; the host's HELLO
  request is the reliable handshake.
- **Opening the port can reset the chip.** Linux asserts DTR+RTS together on open. The
  chip maps (DTR=0, RTS=1) to "pull EN low". pyserial's default open lowers DTR first and
  passes through exactly that state (measured: it reset the panel). `usb_link.open_port`
  uses `SerialPort.open(dtr=False, rts=False)`, which drops both lines in one ioctl
  ((1,1) -> (0,0)), so the chip never sees (0,1); a test replays the ioctls to prove it.
  Lowering RTS first and DTR second is equivalent for the chip. On hardware, six raw opens
  left DTR and RTS low and the chip was not reset.
- **HWCDC flaps `Serial` during a session.** The Arduino `if (Serial)` bool can drop and come back
  during a session (a missed SOF / slow IN poll, no cable event). An unsolicited HELLO on every up edge would read to the
  host as "panel reset": `MODE HOST` plus a 4 s full redraw in the middle of a screen. So `Device::onAttach()` announces
  only when no host session is alive. The panel logs
  `usb: Serial went down` / `came up` for each flap.
- Boot ROM text and IDF log lines can appear on the same port. Hosts must treat
  non-frame bytes as noise (resync handles it).
- The panel only services USB from `loop()`. Wi-Fi is not connected in `setup()` (it is up only
  in idle mode), so HELLO is answered within a second of a reset. Hosts may still retry HELLO for a few
  seconds after opening a freshly reset panel.

## Frame

```
A5 5A | type u8 | seq u8 | len u16 LE | payload[len] | crc32 u32 LE
```

- `crc32` is IEEE CRC-32 (zlib `crc32`, init/xorout `0xFFFFFFFF`) over `type, seq, len, payload`.
  Sync bytes are not covered.
- `len` <= 4096 (`MAX_PAYLOAD`). Max frame 4106 bytes. All integers little endian.
- The CRC covers `seq` and `len` (they sit between `type` and `payload`); every payload with
  variable text puts it **last**, so fixed fields have fixed offsets; HELLO carries
  `max_payload` and `rx_window`.

### Resync

Receiver state: buffer starts at a candidate `A5 5A`. Rules:

1. Bytes before the next `A5 5A` are skipped (a lone trailing `A5` is kept: it may be the
   first half of a sync pair).
2. `len > 4096` -> false header: drop **one** byte, rescan.
3. Not enough bytes yet -> wait.
4. CRC mismatch -> drop **one** byte (not the whole claimed frame), rescan. A real frame
   hiding inside a corrupt one's claimed span is therefore still found.
5. Stall: if a partial frame sits in the buffer with no new byte for 100 ms the panel
   drops it byte-by-byte the same way (`flushStale`), so a dead half-frame cannot swallow
   the next real frames. Host decoders do the same on their own read timeout.

The panel never replies to a frame it could not parse (no trustworthy `seq`); the host
sees a timeout. The panel emits a rate-limited LOG line with error counters.

## Types and payloads

| type | name | dir | payload |
|---|---|---|---|
| 0x01 | HELLO | both | host->panel: empty = "send HELLO"; `proto_ver u16, fw_ver` = "HELLO ack" (see "OTA"). panel->host: below |
| 0x02 | HEARTBEAT | host->panel | empty. ACKed |
| 0x03 | ACK | panel->host | `seq u8` (seq of the acknowledged host frame) |
| 0x04 | NAK | panel->host | `seq u8`, `reason u8` |
| 0x10 | RECT | host->panel | `x u16, y u16, w u16, h u16`, then `h` rows of `w/8` bytes |
| 0x11 | REFRESH | host->panel | `kind u8`: 0 partial, 1 full |
| 0x12 | MODE | host->panel | `mode u8` `[\| idle_timeout_s u16 [\| layout u8]]`: 0 HOST, 1 PANEL (see MODE) |
| 0x20 | IDLE_LAYOUT | host->panel | `total u32 \| offset u32 \| data`: chunked layout store upload (`docs/idle-mode.md`) |
| 0x30 | OTA_BEGIN | host->panel | `size u32, sha256[32], version` (see "OTA"). ACK carries `seq u8, received u32` |
| 0x31 | OTA_DATA | host->panel | `offset u32, data` (1..4092 bytes). ACK carries `seq u8, received u32` |
| 0x32 | OTA_END | host->panel | empty. Panel verifies, switches boot image, ACKs, reboots |
| 0x7F | LOG | panel->host | UTF-8 text, <= 200 bytes, never ACKed. Firmware debug lines, each starting `[<panel millis>] `; a line may span frames. Text queues (2 KiB) while no session exists and is delivered when the next one starts |

**HELLO (panel->host)**, 18 fixed bytes + string:

```
proto_ver u16 | max_payload u16 | rx_window u16 | width u16 | height u16 |
caps u32 | idle_flash_free u32 | fw_ver (rest of payload, UTF-8, no NUL, <= 64)
```

`caps`: bit0 gray4, bit1 idle_storage, bit2 ota (OTA_* frames are handled). The idle firmware reports
gray4 + idle_storage (3) and `idle_flash_free` = 49152, the capacity of the layout store; the OTA receiver
ORs in bit2, so the firmware reports 7.
Real hello seen on hardware: `proto 1, 648x480, max payload 4096, rx window 8192, fw '0.0.0-dev+abc1234'`.

**RECT pixel format.** 1 bit per pixel, rows packed MSB first, **1 = black**, 0 = white.
`x` and `w` multiples of 8, `w >= 1`, `h >= 1`, inside 648x480.
`len == 8 + h * (w/8)` exactly. A full-width band of 50 rows is 4058 bytes, so a full
frame is 10 RECTs (9 x 50 rows + 30). The same 1 = black convention is used by the
emulator's PBM output (PBM P4 is 1 = black). PNG output is inverted accordingly.

**MODE.** Payload `mode u8 [| idle_timeout_s u16 [| layout u8]]`; lengths 1, 3 or 4 (anything else is NAK
BAD_LEN). All arguments are validated before any is applied (a refused MODE changes nothing).

- `mode` `HOST` (0): the host owns the display; RECT/REFRESH are served. `PANEL` (1): show the idle layout
  now and **stay in it** (pinned idle, what the engine sends on `stop()`): RECT and REFRESH are NAKed
  `WRONG_MODE`, heartbeats are still ACKed and do not wake it, only `MODE HOST` leaves it.
- `idle_timeout_s`: silence timeout before the panel enters idle by itself. `0` keeps the current value;
  otherwise 15..3600 (else NAK BAD_ARG). **Default 60 s if never set.** A non-zero value is
  **persisted** in NVS (`inktf/idle_to`) and survives a reboot. The host heartbeats every 5 s, so the
  15 s floor tolerates two late ticks; "silence" is counted from the last CRC-valid frame of any type.
- `layout`: `0xFF` keeps the selection, otherwise an index into the stored layouts (BAD_ARG if there is no
  such layout or no store). Not persisted.

Display ownership has three states (details and diagram in `docs/idle-mode.md`): WAIT (boot, nobody owns
the glass; RECT/REFRESH NAK `WRONG_MODE` until `MODE HOST`), HOST, IDLE. In HOST, `idle_timeout_s` of
silence moves to IDLE (auto); in WAIT the same clock runs from boot. **Any valid frame wakes an auto
idle** and the panel returns to the state it came from (HOST keeps the host's shadow framebuffer; the
glass shows the idle picture, so the next REFRESH is promoted to a full one). Switching to `HOST` does
not touch the glass: send a full REFRESH first so the panel's own screen is replaced.

**Host suspend.** `MODE PANEL` is also the "idle now" the host sends when the machine goes to sleep (logind `PrepareForSleep`),
`MODE HOST` plus a full REFRESH is the way back; no separate value exists. If the host disappears without saying so, the panel
notices a USB bus that has stopped (no SOF packets for 2 s, not during an OTA transfer) and enters auto idle without waiting for
`idle_timeout_s`; it also ends the host session, so the first valid frame after the host is back is answered with an unsolicited
HELLO and the host redraws everything. See `docs/idle-mode.md` "Sleep and wake".

**REFRESH.** Panel blocks until the e-paper is done, then ACKs. Partial refresh covers
the bounding box of all RECTs since the last REFRESH (the panel keeps a 38,880-byte
shadow framebuffer of the host image and a second one of what is on the glass). Partial with
nothing dirty is a no-op ACK. Full refresh repaints all 648x480. A partial sent before any
full refresh of the session is promoted to a full one (the panel does not know what is on
the glass). Timing: partial ~0.67 s, full ~4.0 s (`docs/partial-refresh.md`). The host
owns the ghosting policy (full refresh every N partials; the BLE firmware used 30).

**NAK reasons:** 1 BAD_TYPE, 2 BAD_LEN, 3 BAD_RECT (zero size, unaligned, out of bounds),
4 BAD_ARG, 5 WRONG_MODE, 6 UNSUPPORTED, and for OTA: 7 OTA_BAD_OFFSET, 8 OTA_FLASH_FAIL,
9 OTA_HASH_MISMATCH, 10 OTA_TOO_BIG, 11 OTA_BUSY, 12 OTA_NO_SESSION, 13 OTA_BAD_IMAGE, and for idle
mode: 14 BAD_DATA (idle store failed validation), 15 STORAGE (flash write failed).
Check order inside RECT/REFRESH: mode, length, geometry, exact length.

### IDLE_LAYOUT

`total u32, offset u32, data...` (data <= 4088 B), one ACK per chunk, committed (validated and written to
flash) when `offset+len == total`; the final ACK is the commit. Needs `caps` bit1. Format, validation and
failure behaviour: `docs/idle-mode.md`. Without idle hooks (the host test build) it is NAK UNSUPPORTED.
Numbering is shared by Python and C++; `decky/tests/test_proto_numbering.py` parses `usbproto.h` and fails
if any frame type, NAK reason or caps bit differs or collides.

### OTA (firmware update over USB)

Code: panel `firmware/src/ee05/ota_usb.{h,cpp}` (state machine, hooked in through
`usbproto::Ext`), host `decky/py_modules/inkterface_core/ota/stream.py`, emulator
`EmulatedPanel(ota_slot=...)`. Release format, trust model and rollback: [`ota.md`](ota.md).
Requires `caps` bit2; firmware without it NAKs these frames UNSUPPORTED.

```
OTA_BEGIN  size u32 | sha256[32] | version (1..32 bytes, printable ASCII, no NUL)
OTA_DATA   offset u32 | data (1..4092 bytes)
OTA_END    (empty)
ACK to BEGIN / DATA:  seq u8 | received u32      ACK to END: seq u8
```

`received` is how many bytes of the image the panel holds, always a clean prefix `[0, received)`.

- **BEGIN** starts a session for one image (`size`, `sha256`, `version`). The panel
  prepares the *inactive* OTA slot (erase happens lazily as data arrives, so BEGIN is
  instant). A BEGIN with the **same** three values as the open session is a **resume**: nothing
  is touched and `received` tells the host where to continue. Different values abort the old
  session and start over at 0. Sessions survive host silence and host reconnects; they end at
  reboot, on a different BEGIN, or on any failed transfer.
- **DATA** must continue exactly at `received` (`offset == received`). Host windows its DATA
  frames like RECTs (`rx_window`; two 4094-byte frames fit in 8192). A chunk that was already
  stored in full is a resend and is ACKed with the unchanged `received`, so retransmitting
  after a timeout is safe. Anything else is NAKed.
- **END** requires `received == size`. The panel reads the slot **back from flash**, hashes it
  and compares with the BEGIN `sha256`, then asks the ESP-IDF OTA layer to validate the image
  and flip the boot partition. Only then does it ACK. It reboots ~400 ms later so the ACK
  can leave the FIFO. A resent END after success is ACKed again.
- The running image is never written. Until END succeeds the panel still boots the old one:
  power loss, a dropped cable or any NAK leaves the old firmware bootable.

NAK table (check order is the order listed per frame; the firmware, the emulator and
`decky/tests/test_ota_cpp.py` agree byte for byte):

| frame | reason | when |
|---|---|---|
| BEGIN | BAD_LEN | payload < 37 or > 68 bytes |
| BEGIN | OTA_BUSY | an image was already committed (reboot pending) |
| BEGIN | BAD_ARG | `size == 0` or a non-printable version byte |
| BEGIN | OTA_TOO_BIG | `size` > OTA slot (0x330000) |
| BEGIN | OTA_FLASH_FAIL | slot could not be prepared |
| DATA | BAD_LEN | payload < 5 bytes |
| DATA | OTA_BUSY / OTA_NO_SESSION | committed / no BEGIN yet (or session was dropped) |
| DATA | OTA_BAD_OFFSET | `offset > received`, or it overlaps held data without being a full resend |
| DATA | OTA_TOO_BIG | `offset + len > size` |
| DATA | OTA_FLASH_FAIL | flash write failed. The session is dropped, restart with BEGIN |
| END | BAD_LEN | payload not empty |
| END | OTA_NO_SESSION | no open session |
| END | OTA_BAD_OFFSET | `received != size` (host thinks it is done, panel disagrees: resync with BEGIN) |
| END | OTA_FLASH_FAIL | read-back failed |
| END | OTA_HASH_MISMATCH | flash content hash differs from BEGIN `sha256`. Session dropped |
| END | OTA_BAD_IMAGE | not a bootable ESP32 app image (`esp_ota_end`). Session dropped |

**Marking the new image valid (HELLO ack).** After the reboot the new image is *pending
verification*. The host reconnects, sends the usual empty HELLO request and checks the reply:
`proto_ver` and `fw_ver` must be what the manifest promised. If it accepts, it sends a HELLO
with a payload, `proto_ver u16 | fw_ver (as reported)`. The panel answers it like any HELLO
and, if both fields match what it reported, marks the image valid and cancels the rollback
timer. Anything else (no ack, mismatching ack, no host at all within 180 s) lets the verify
timer expire and the bootloader rolls back to the previous image. A host must therefore not
send the ack for a version or protocol it does not accept. Images that were *not* installed
over USB (legacy Wi-Fi OTA) are marked valid at boot as before.

## Sequencing, ACKs, timeouts

- The host numbers its frames with an incrementing `seq` (wraps at 256). The panel numbers
  its own frames independently. A panel ACK/NAK carries the host seq in its payload.
- Every host frame except ACK/NAK/LOG is answered with exactly one ACK or NAK, in order.
  ACK for RECT means "copied into the shadow buffer". ACK for REFRESH means "e-paper done".
- All commands are idempotent, so the host may resend the same `seq` after a timeout.
  Suggested timeouts: 1 s for everything except REFRESH, 15 s for REFRESH,
  and 5 s for any frame sent while the panel may be drawing its idle screen (it cannot service USB during
  a ~4.1 s full refresh; the frame waits in its RX queue and the retry is idempotent) (full is ~4 s,
  partial ~0.7 s).
- **Session.** The first valid frame after silence starts a session. If it is not a HELLO
  the panel sends a HELLO before handling it, so the host always learns geometry/limits.
- **Heartbeat.** Host sends HEARTBEAT every 5 s while idle (any valid frame counts). After
  `idle_timeout_s` (default 60 s, `MODE`) without a valid frame the panel enters idle: it draws its
  stored layout (`docs/idle-mode.md`; a full refresh, ~4 s during which USB is not serviced) and ends the
  USB session (the next frame gets a fresh HELLO). The host's ACK/heartbeat tolerance is therefore
  `idle_timeout_s` (>= 15 s).

### Flow control

Because the USB layer drops on overflow, the host keeps at most `rx_window` **wire bytes**
unacknowledged (HELLO field; 8192 today: half of the 16 KiB RX queue). Sliding window:
send the next frame only if `sum(unacked wire bytes) + len(next) <= rx_window`;
a single frame is always allowed when nothing is in flight. `Link.send_acked` implements
this. After a REFRESH, wait for its ACK before sending more: the panel is blocked for
0.7-4 s and its RX queue is the only buffer.

## Host side notes

- **Serial access.** Everything in `inkterface_core` is stdlib-only. pyserial is NOT usable
  inside Decky v3.3.1 (its Linux backend needs `termios`), so `usb_link.py`,
  `decky/tools/send_test.py` and `decky/tools/edge_test.py` all use `inkterface_core/serialport.py`
  (raw tty via `fcntl`/`select`). The DTR/RTS rule (never present DTR=0, RTS=1) is kept
  by `usb_link.open_port`.
- `proto.Decoder` mirrors the panel parser, including `flush_stale()`. Call it when the
  read times out with a partial frame pending.
- `diff.diff_rects(prev, new)` -> 8-px-aligned rects, each <= 4096-byte payload. Greedy
  row merge (merge when it costs fewer bytes than another 18-byte RECT overhead). Covers
  every changed pixel; not guaranteed minimal.
- `emulator.EmulatedPanel.feed(bytes) -> reply bytes` behaves like the firmware (tested
  byte for byte against the C++ code) and writes a PNG per REFRESH (PBM if Pillow is
  missing).
- Discovery: match USB VID `303a` (`usb_link.find_panels`, optional serial-number
  substring). Never "first `/dev/ttyACM*`": game controllers also enumerate as ttyACM.

## Throughput estimate and measurement

Wire bytes for one full frame: 38,880 pixel bytes + 10 x (6 hdr + 4 crc + 8 rect hdr)
= **39,060**. A 200x96 box is 2,418 bytes; a 64x32 box is 274.

| | estimate | measured (this dev machine, EE05) |
|---|---|---|
| USB-Serial-JTAG ceiling | full speed 12 Mbit/s, 64 B packets: ~1 MB/s theoretical | **~234 KB/s** (identical for windows 4200, 8192, 12288 bytes: link-bound, not window-bound) |
| full frame transfer | 39 KB / 234 KB/s | **167 ms** |
| 200x96 rect transfer | 2.4 KB | **10 ms** |
| 64x32 rect transfer | 0.27 KB | **1.3 ms** |
| HEARTBEAT / tiny-RECT round trip | | **0.6-1.2 ms** (median 1.0) |
| REFRESH full (e-paper) | ~4 s | **4.1 s** |
| REFRESH partial (e-paper) | ~1-2 s | stock GxEPD2: **1.8 s** for 200x96 and 64x32. Tuned firmware: **0.67 s** (`docs/partial-refresh.md`) |

So the e-paper dominates: a partial update is ~0.67 s refresh (was 1.8 s) + a few ms of transfer; a
full frame is ~4.1 s + 0.17 s. The ~234 KB/s link rate is not the bottleneck at any
realistic dirty-rect size. (Numbers measured against a Linux dev machine, not the Steam
Machine; expect the same order of magnitude.)

## Hardware test procedure

For anyone with a panel. Prerequisites: PlatformIO (`pio`), Python 3 (no pip packages: the tools use the stdlib
`inkterface_core.serialport`). Port:
`/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_*-if00` (never ttyACM0/1 on a
machine with game controllers; `send_test.py --list` shows candidates by VID).

**0. Back up the panel first** (the flash holds the Wi-Fi password and Steam key; keep the
backup private, never commit it). A full backup is the whole 16 MB flash
(16,777,216 bytes; partitions nvs/otadata/app0/app1 on the original firmware).

```bash
PORT=/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_AA:BB:CC:DD:EE:FF-if00
cd firmware
pio pkg exec -p tool-esptoolpy -- esptool.py --chip esp32s3 --port $PORT --baud 921600 \
    read_flash 0 ALL ~/ee05-full-$(date +%Y%m%d).bin
```

**Restore** (whole flash, including NVS and the original firmware, ~4 min):

```bash
pio pkg exec -p tool-esptoolpy -- esptool.py --chip esp32s3 --port $PORT --baud 921600 \
    write_flash 0 <backup.bin>
```

**1. Flash the firmware** (leaves NVS untouched; rewrites bootloader, partition table, boot_app0, app):

```bash
cd firmware
cp -n src/ee05/idle_config.h.example src/ee05/idle_config.h   # or use your real idle_config.h
pio run -e ee05 -t upload --upload-port $PORT
```

With the example config the panel cannot join your Wi-Fi, so `setup()` takes ~25 s before
USB answers. With your real `idle_config.h` it is much shorter.

**2. Run the tests** (from the repo root):

```bash
python3 decky/tools/send_test.py      # handshake, RTT, throughput, pattern + partial refreshes
python3 decky/tools/edge_test.py --slow   # garbage, bad CRC, split writes, bad rects, MODE edge cases, silence -> idle -> wake
python3 decky/tools/idle_test.py          # idle mode end to end (uploads each built-in layout, ~7 min, docs/idle-mode.md)
```

**Success looks like:** every line `[ OK ]`, final `RESULT: ALL OK`; the panel shows the
test pattern (border, diagonals, dithered gradient strip, checkerboard), then a 200x96
inverted box, then a 64x32 black box; then it hands back and repaints its own screen.
Expected numbers: HELLO `648x480`, RTT ~1 ms, throughput ~230 KB/s, full refresh ~4 s,
partial ~0.67 s (`send_test.py --partials 30` prints median/p90).

**If it fails:** `no HELLO` -> wait ~30 s after reset, check the by-id path, make sure
nothing else (ModemManager, a serial monitor) holds the port. `timeout waiting for RECT
ACKs` with `panel log: rx errors` -> bytes were lost (window too large). Board reboots when
the port opens -> something other than `usb_link.open_port` opened it. Restore with the
command above.

A firmware built with the example `idle_config.h` has idle mode (`docs/idle-mode.md`), but Wi-Fi fetches fail until the
store carries real credentials. The panel's NVS (Wi-Fi/Steam provisioned by the old BLE app) is not
touched by this flash.

## Measured on hardware / tested

Host-only (pytest `decky/tests`, see CI for the current count; plus the C++ parser compiled with g++ and run
against the Python emulator on the same byte streams at chunk sizes 1, 7, 64, whole):
golden bytes, CRC vector, max payload, oversize `len` header, bad CRC then good frame,
corrupt frame containing a real frame, garbage prefix, lone/duplicated `A5`, `A5 5A` inside
a payload, every possible single split point, random split reads, stalled half-frame,
zero-size rect, unaligned x/w, out-of-bounds rect, short rect data, wrong mode, unknown and
reserved types, host timeout, diff -> RECT -> emulator exact reproduction (random frames,
single pixel, full change, identical frames), PNG and PBM output, `send_test.py` end to end.

Also on the real panel (`edge_test.py --slow`): garbage prefix, bad CRC, `len=65535`
header, stalled 4096-byte half frame, RECT written one byte at a time, zero width/height,
unaligned x/w, past right/bottom edge, short data, unknown type, OTA_BEGIN -> UNSUPPORTED (on firmware without OTA),
RECT in PANEL mode, 15 s host timeout -> new session + back to PANEL mode, plus
`send_test.py` (full + two partial refreshes, throughput, RTT).

**Needs a person looking at the panel / not verifiable by the test tools:** the picture on the glass (pattern
correctness, ghosting after repeated partials), long soak (hours of 5 s heartbeats), and behaviour with real
Wi-Fi credentials. The Steam Machine's USB host behaviour is checked on the device itself.

**Hardware-only (cannot be emulated here):** `main.cpp` glue (`EpdPanel::present`
pixel replay into GxEPD2, `usbPoll`, loop gating, repaint after host release), HWCDC RX
queue overflow behaviour, DTR/RTS reset behaviour, e-paper refresh timing and the picture
itself. The glue was exercised on a dev-machine panel and on a Steam Machine. Everything else, including the real firmware parser/device (`usbproto.cpp`
compiled on the host under ASan/UBSan during development), is covered by tests.
