# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/). The version lives in the [`VERSION`](VERSION) file.

## [Unreleased]

### Changed

- The enclosure is a remix of Valve's case, modified for the EE05, and is published on Printables under CC BY-NC-SA 4.0. Valve's original design stays MIT;
  the code in this repository stays MIT. The enclosure files are not in this repository.

## [1.0.0-beta.2] - 2026-11-12

### Fixed

- Gray blocks and haze around values updating on a busy screen (Telemetry): the field on unchanged pixels was not neutral. The partial refresh left pixels it only
  covered (white areas, hatched fills, bars inside the window) under a steady 2 V field, because `VCOM_DC` was -2.0 V while their
  source stayed at GND; it also made black->white 27 % weaker than white->black. `VCOM_DC` is now -0.1 V (`0x82` = `00`).
  See [docs/partial-refresh.md](docs/partial-refresh.md), "Pixels the window only covers".

## [1.0.0-beta.1] - 2026-11-12

First release of the USB + Decky stack. This is a beta: the code is covered by automated tests, and the backend
and the plugin pages were exercised on a Steam Machine.

### What v1 is compared with the Valve original ([their repository](https://gitlab.steamos.cloud/SteamHardware/SteamMachine/inkterface))

The original was a Qt desktop app on a PC that drove an Adafruit Feather ESP32-S3 over Bluetooth Low Energy.
v1 replaces all of it:

- **Transport:** USB. The panel is a USB serial device on the Steam Machine (no Bluetooth, no pairing, no app on a
  second computer). The wire protocol is versioned and documented in [docs/usb-protocol-v1.md](docs/usb-protocol-v1.md).
- **Host:** a Decky Loader plugin runs on the Steam Machine. It renders screens, diffs frames and sends only the changed
  regions. The Python core is standard library plus Pillow; there is no pyserial because Decky's Python bundle has no
  `termios` ([docs/decky-runtime.md](docs/decky-runtime.md)).
- **Hardware:** Seeed XIAO ePaper Display Board EE05 (ESP32-S3) with the 5.83" 648x480 UC8179 panel. The Feather firmware is
  no longer in the tree; Valve's original build is in [their repo](https://gitlab.steamos.cloud/SteamHardware/SteamMachine/inkterface).
- **Fast partial refresh:** a bare-metal single-phase UC8179 driver replaces the stock partial waveform
  ([docs/partial-refresh.md](docs/partial-refresh.md)). A changed value repaints in a median of about 0.7 s
  (stock waveform: about 1.8 s). Screens where several values change at once repaint in about 0.8 s at the median.
- **Live screens** (drawn by the plugin): Telemetry, Thermals & Power, Big Clock and Now Playing. A separate "In a game"
  list can replace the normal rotation while a game runs, each list with its own order, timing and pin.
- **Standby screens** (drawn by the panel when the machine is off, asleep or the plugin stops): Clock & Weather,
  Clock & Weather (4 grays), Minimal Clock and Steam Stats. They are configured from the plugin (Wi-Fi, location, Steam).
- **Screens are data:** JSON (`screen.json`, optionally one `screen.py`), checked by a validator, renderable with no
  hardware through the host PNG emulator, shareable as zip packages. Code screens need the user's approval of the exact
  code ([docs/screen-packages.md](docs/screen-packages.md)).
- **Screen Store:** community screens live in a separate repository whose CI validates them and publishes a signed
  `index.json` (Ed25519, with its own key) plus one zip and one preview per screen. The plugin's **Screen Store** page
  shows thumbnail, author, version and badges, with Install / Update / Uninstall. The signature is checked before the
  index is parsed, a serial rollback is refused, zip size and sha256 are checked against the signed index, and code
  screens install disabled until approved. It works offline from the saved catalog
  ([docs/screen-catalog.md](docs/screen-catalog.md)).
- **Signed firmware updates:** the plugin finds `fw-v*` GitHub releases, verifies an Ed25519 signature over the manifest,
  and streams the image over USB. A panel that does not confirm the new image within about 3 minutes rolls back by itself
  ([docs/ota.md](docs/ota.md)).
- **Sleep and wake:** the panel switches to its standby screens when the machine suspends and back after resume.
- **Pages:** Live Screens, Standby Screens, Screen Store (with an Installed tab), Firmware, Settings and About. Live and
  Standby rows have an order track, L1/R1 to move a focused row, X for Reorder mode, a "..." menu and a settings sheet;
  every page leaves room for Steam's footer bar ([docs/plugin-ui.md](docs/plugin-ui.md)).
- **Settings made for a controller.** Weather: search a town by name (Open-Meteo geocoding, no key) or use an approximate
  location from the IP address (explicit button, ipwho.is, no key); units default from the system locale. Steam: use the
  signed-in account, or paste a profile link, SteamID64, SteamID3, SteamID2 or a custom name; the API key page has a QR
  code and a "Test key" button, and the key never leaves the backend. Panel Wi-Fi: use the Steam Machine's current
  network name (nmcli, no sudo); the password still has to be typed.
- **Developer mode** (off by default) enables importing a zip or an https link as a screen package.
- **Ghost clearing:** the full refresh that clears ghosting is time-based (`ghost_clear_minutes`, 0 to 120, default 10,
  0 = never) and runs at a quiet moment ([docs/ghost-clear.md](docs/ghost-clear.md)).
- **4-gray idle screens** use the panel maker's own 4-gray waveform (stored in the controller's OTP, selected with
  `0xE0`/`0xE5` as in Good Display's GDEY0583T81 demo). `E0`/`E5` are written before `PON`, so the temperature sensed at
  power-on is used for the right waveform; stripes in the darkest gray are barely visible with this order
  ([docs/gray-waveform.md](docs/gray-waveform.md)). No third-party waveform
  bytes remain in the repository.
- **Text rendering:** FreeType's auto-hinter is used on the panel and in the plugin, so every letter of a size has the same
  stem width. The bundled font is **Inkterface Mono**, IBM Plex Mono with the TrueType hinting instructions removed
  and renamed, as the SIL OFL requires (`decky/py_modules/inkterface_core/screens/fonts/FONTLOG.txt`,
  [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)).
- **Reproducible builds:** the firmware build is pinned (`espressif32@7.1.3`, `ArduinoJson@7.4.3` and pinned
  Arduino-ESP32 and esptool packages), and Pillow is fetched with a pinned sha256 by `decky/scripts/vendor.sh` instead of
  being committed.
- **Release tooling:** key generation, manifest signing and offline verification (`tools/release/`), firmware and plugin
  release workflows, [docs/RELEASING.md](docs/RELEASING.md). The `VERSION` file is the single release version and is
  checked against `decky/package.json` and the release tags. A pre-release plugin also offers pre-release firmware
  (`fw-v*-beta.*`); a stable plugin ignores them.

### Known limits

- **Busy screens have a long tail.** A value that changes while a 4 s ghost-clear refresh runs waits for it (about 1 in 14
  changes on Telemetry). Screens with one changing value
  stay near 0.65 s.
- **Installing the plugin needs Decky Developer mode** (Install plugin from ZIP file) and root on the Steam Machine
  (`~/homebrew/plugins` is root-owned). The first release is a GitHub zip, not a Decky store plugin.
- **No real GitHub release has been exercised end to end.** Firmware updates were tested against a fake GitHub with a
  test key, and on a real panel through the same test seam. The first tagged release is the first real run.
- **The panel does not verify signatures.** Anyone who can open its serial port can flash it. The signature protects the
  update path from GitHub, not the panel from local access ([SECURITY.md](SECURITY.md)).
- **Wi-Fi password and Steam API key sit unencrypted in the panel's flash**, and the panel's TLS client does not validate
  certificates ([docs/idle-mode.md](docs/idle-mode.md#security-and-known-limits)).
- **Code screens are not sandboxed.** Approving one lets it run as the plugin's user.

### Planned

- Decky plugin store submission (not part of this release).
- The first signed firmware release.

[Unreleased]: https://github.com/FliesWithWind/inkterface/compare/plugin-v1.0.0-beta.1...HEAD
[1.0.0-beta.1]: https://github.com/FliesWithWind/inkterface/releases/tag/plugin-v1.0.0-beta.1
