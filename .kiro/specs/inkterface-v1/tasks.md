# Implementation plan: Inkterface v1

Tasks are checked where the repository contains the implementation and its tests. Unchecked tasks are not done yet or
are not recorded in this repository. Requirement numbers refer to `requirements.md`.

- [x] 1. USB protocol and transport
  - [x] 1.1 Define protocol v1 (framing, CRC32, resync, HELLO, ACK/NAK, RECT, REFRESH, MODE, IDLE_LAYOUT, OTA, LOG) in `docs/usb-protocol-v1.md`
    - _Requirements: 1.3, 1.5, 1.7_
  - [x] 1.2 Host codec in `decky/py_modules/inkterface_core/proto.py` and firmware parser in `firmware/src/ee05/usbproto.cpp`, compared byte for byte in tests
    - _Requirements: 1.5_
  - [x] 1.3 Stdlib-only serial port (`serialport.py`) and panel discovery by VID `303a` (`usb_link.py`), opening without resetting the chip
    - _Requirements: 1.1, 1.2, 1.6_
  - [x] 1.4 Handshake and protocol-version refusal in the engine
    - _Requirements: 1.3, 1.4_

- [x] 2. Active-mode rendering
  - [x] 2.1 Dirty-rectangle diff aligned to 8 pixels and split to 4096 byte payloads (`diff.py`)
    - _Requirements: 2.2, 2.3_
  - [x] 2.2 Firmware-equivalent emulator and PNG output (`emulator.py`, `decky/tools/run_engine.py`)
    - _Requirements: 4.8_
  - [x] 2.3 Engine: render, diff, send loop, event-driven rendering, NAK handling, reconnect
    - _Requirements: 2.1, 2.5, 7.3_
  - [x] 2.4 Refresh and ghost-clear policy (`ghost_clear_minutes`, quiet-moment full refresh)
    - _Requirements: 2.5, 2.6_
  - [x] 2.5 Sleep and wake: hand the panel to its standby screens on suspend and back on resume
    - _Requirements: 2.7_

- [x] 3. Fast partial refresh on the EE05
  - [x] 3.1 Replace the stock waveform with the bare-metal UC8179 driver and single-phase fast partial (`uc8179_fast.h`, `fastpartial.h`)
    - _Requirements: 2.4_
  - [x] 3.2 Document every register and table value against the panel datasheet (`docs/uc8179-tables.md`) and pin the bytes with a golden test
  - [x] 3.3 Neutral field for unchanged pixels (`VCOM_DC` set to -0.1 V) with a panel model test
    - _Requirements: 2.4_
  - [x] 3.4 Measure repaint time on a real panel: median about 0.7 s (`docs/partial-refresh.md`, `CHANGELOG.md`)
    - _Requirements: 2.4, 10.5_

- [x] 4. Screen format and engine
  - [x] 4.1 `screen.json` validator, widget renderer, fonts and icons (`screens/`)
    - _Requirements: 4.1, 4.2_
  - [x] 4.2 Built-in live screens: Telemetry, Thermals & Power, Big Clock, Now Playing, with golden images
    - _Requirements: 5.1, 5.3_
  - [x] 4.3 Host data sources: time, system, local Steam, Steam Web API, weather, with change events and fast sampling
    - _Requirements: 7.1, 7.2, 7.3_
  - [x] 4.4 Scheduler: pin, rotation, interval, Now Playing on game launch, separate in-game list
    - _Requirements: 6.1, 6.2, 6.3_
  - [x] 4.5 Settings file with atomic saves, validation and per-screen settings
    - _Requirements: 6.4, 6.5_

- [x] 5. Idle mode
  - [x] 5.1 Idle layout wire format and flash store (`docs/idle-mode.md`, `firmware/src/ee05/idle_store.cpp`)
    - _Requirements: 3.3, 3.7_
  - [x] 5.2 Host compiler, planner and uploader (`inkterface_core/idle/`), including the free-flash check
    - _Requirements: 3.8_
  - [x] 5.3 Panel renderer and painter for mono and gray4, with firmware renderer golden tests
    - _Requirements: 3.6_
  - [x] 5.4 Panel data sources over Wi-Fi: NTP clock, Open-Meteo weather, Steam, HTTPS JSON; keep last good values
    - _Requirements: 3.4, 3.5_
  - [x] 5.5 Idle timeout, entry on silence, leave on first host frame
    - _Requirements: 3.1, 3.2_
  - [x] 5.6 Built-in idle screens: Clock & Weather (mono and 4-gray), Steam Stats, Minimal Clock
    - _Requirements: 5.2, 5.3_
  - [x] 5.7 4-gray waveform from the controller's own OTP (`docs/gray-waveform.md`), no third-party bytes in the repository
    - _Requirements: 3.6_

- [x] 6. Screen packages and the Screen Store
  - [x] 6.1 Zip importer with path, size, entry-type and ratio limits and atomic install
    - _Requirements: 4.3, 4.7_
  - [x] 6.2 Approval flow for code screens, revocation on change, separate-process runner with limits
    - _Requirements: 4.4, 4.5, 4.6_
  - [x] 6.3 Developer mode gate for zip and URL import
    - _Requirements: 4.9_
  - [x] 6.4 Signed catalog client: signature before parse, serial rollback refusal, size and sha256 checks, offline use
    - _Requirements: 4.3_
  - [x] 6.5 Authoring docs, examples and the `emulate` CLI (`docs/screen-format.md`, `docs/screen-packages.md`, `decky/examples/`)
    - _Requirements: 4.8_

- [x] 7. Decky plugin
  - [x] 7.1 Python wrapper `decky/main.py` and `PluginCore`
  - [x] 7.2 React frontend: Live Screens, Standby Screens, Screen Store, Firmware, Settings, About, quick panel
    - _Requirements: 6.1, 6.4_
  - [x] 7.3 Controller-first layout: reorder with L1/R1 and X, wrapping button bars, room for Steam's footer bar
  - [x] 7.4 Wi-Fi, weather, location and Steam account settings with secrets that are never read back
    - _Requirements: 6.6_
  - [x] 7.5 Plugin zip build (`decky/scripts/make_zip.py`) with vendored Pillow and the static idle-preview renderer

- [x] 8. Signed firmware OTA
  - [x] 8.1 Release lookup with ETag cache and rate-limit handling
    - _Requirements: 8.1_
  - [x] 8.2 Ed25519 verification, manifest schema and policy (downgrade, protocol, minimum plugin)
    - _Requirements: 8.2, 8.3_
  - [x] 8.3 Streaming install with resume, cancel and confirm-boot; engine paused while it runs
    - _Requirements: 8.4, 8.5, 8.8_
  - [x] 8.4 Firmware OTA receiver: hash check, inactive-slot write, mark valid on host acknowledgement, 180 s rollback
    - _Requirements: 8.6, 8.7_
  - [x] 8.5 Key generation, signing and offline verification tools (`tools/release/`)
    - _Requirements: 9.3_

- [x] 9. Quality gates and releases
  - [x] 9.1 CI jobs `pytest`, `firmware` and `plugin` (`.github/workflows/ci.yml`)
    - _Requirements: 9.1_
  - [x] 9.2 90 percent coverage gate on the OTA code and the firmware updater
    - _Requirements: 9.2_
  - [x] 9.3 Release workflows for tags `fw-v*` and `plugin-v*`, version checked against `VERSION` and `decky/package.json`
    - _Requirements: 9.3, 9.4_
  - [x] 9.4 File-size cap, docs path check, licence and third-party notice tests
    - _Requirements: 9.5, 9.6_
  - [x] 9.5 Documentation, `CHANGELOG.md` and `THIRD_PARTY_NOTICES.md`

- [x] 10. Hardware verification on a real Steam Machine (Requirement 10)
  Verified on a Steam Machine by the maintainer, 2026-11-12 (8/8 section 12 criteria).
  The last criterion, OTA from the signed GitHub release (beta.1 to 1.0.0-beta.2), was confirmed with no rollback.
  - [x] 10.1 The plugin installs from the zip in Decky developer mode
    - _Requirements: 10.1_
  - [x] 10.2 The plugin finds the panel over USB and the handshake passes
    - _Requirements: 10.2_
  - [x] 10.3 Four live screens and three idle screens render on the glass
    - _Requirements: 10.3_
  - [x] 10.4 Mode switch works both ways (live to idle and back) on the Steam Machine
    - _Requirements: 10.4_
  - [x] 10.5 A changed value repaints in under 1 second (median about 0.7 s)
    - _Requirements: 10.5_
  - [x] 10.6 A third-party screen zip imports and runs on the device
    - _Requirements: 10.6_
  - [x] 10.7 OTA from a real GitHub release succeeds
    - _Requirements: 10.7_
  - [x] 10.8 A forced rollback recovers on real hardware
    - _Requirements: 10.8_

- [ ] 11. Planned, not part of v1
  - [ ] 11.1 Submit the plugin to the Decky store
  - [x] 11.2 Publish the first signed firmware release (`fw-v1.0.0-beta.2`, a pre-release, installed by OTA on a Steam Machine)
  - [ ] 11.3 Real Wi-Fi soak of idle sources, and a long idle and sleep/wake soak (`docs/idle-mode.md`, "Not yet verified")
