# Requirements: Inkterface v1

Derived from the v1 design written on 10 November 2026 (Decky plugin plus modular screens, rebuilt on USB) and from
what the repository implements today. Acceptance criteria use EARS notation (WHEN / WHILE / IF ... THEN the system
SHALL ...). "Host" is the machine running the Decky plugin, "panel" is the XIAO EE05 board with its 648x480 display.
Where the shipped behaviour differs from the original design, the design document notes it (see `design.md`, "Changes
from the 10 November design").

## Introduction

Inkterface is a small e-ink status panel that sits next to a Steam Machine. The v1 stack replaces the earlier
Bluetooth and Qt-app stack of Valve's original Inkterface: the panel connects over USB only, a Decky Loader plugin
renders screens while the machine is on, the panel draws its own standby screens while the machine is off, screens are
data packages the community can write, and firmware updates are signed and installed over the same cable.

## Requirements

### Requirement 1: USB transport and handshake

**User Story:** As a Steam Machine owner, I want the panel to work over one USB cable, so that I need no battery, pairing
or second computer.

#### Acceptance Criteria

1. WHEN the plugin starts or the panel is plugged in, THEN the system SHALL find the panel by USB vendor id `303a` and SHALL NOT open any other serial device.
2. WHEN the host opens the panel's serial port, THEN the system SHALL set DTR and RTS low in one operation so that the chip is not reset.
3. WHEN the host requests a HELLO, THEN the panel SHALL answer with its protocol version, firmware version, resolution, capability bits and free idle flash.
4. IF the panel's protocol version differs from the host's, THEN the system SHALL refuse to talk, send no frames, and point the user to the firmware update.
5. IF a frame fails its CRC or has an oversized length, THEN the receiver SHALL resynchronise on the next `A5 5A` without losing a valid frame that sits inside the corrupt span.
6. The system SHALL use only the Python standard library for serial I/O, because Decky's bundled Python has no `termios`.
7. WHEN the host sends a frame that needs an answer, THEN the panel SHALL reply with ACK or NAK carrying the sequence number of that frame.

### Requirement 2: Active mode rendering

**User Story:** As a user, I want live stats to change on the panel quickly, so that the display feels live.

#### Acceptance Criteria

1. WHILE the host is alive, the host SHALL render the full 648x480 1-bit frame with Pillow and diff it against the last frame sent.
2. WHEN a frame differs from the last one, THEN the host SHALL send only dirty rectangles whose `x` and `w` are multiples of 8, followed by a partial REFRESH.
3. WHEN a rectangle payload would exceed 4096 bytes, THEN the host SHALL split it on row boundaries.
4. WHEN a displayed value changes, THEN the panel SHALL show it in under 1 second at the median, measured on a real panel.
5. WHEN the screen changes, the panel reconnects, or a NAK forces a resync, THEN the host SHALL send the full frame and a full REFRESH.
6. WHILE time since the last full refresh exceeds `ghost_clear_minutes` and at least 30 partial refreshes have happened, the host SHALL run a full refresh at the first quiet moment, and SHALL force one at the latest after 1.5 times that interval.
7. WHEN the host sleeps, THEN the system SHALL stop sending frames and hand the panel to its standby screens, and SHALL restore live screens on resume.

### Requirement 3: Standby (idle) mode on the panel

**User Story:** As a user, I want the panel to show something useful while the machine is off or asleep, so that it
does not sit blank.

#### Acceptance Criteria

1. WHEN the panel receives no valid host frame for `idle_timeout_s` (default 60, range 15 to 3600), THEN the panel SHALL draw its stored idle layout by itself.
2. WHEN the first valid host frame arrives, THEN the panel SHALL leave idle mode and the host SHALL send a full frame.
3. The panel SHALL store idle layouts in flash so that it can render with no host present.
4. WHILE in idle mode, the panel SHALL connect to Wi-Fi without blocking USB service and SHALL fetch data from the clock (NTP), weather (Open-Meteo), Steam, and generic HTTPS JSON sources.
5. IF a data fetch fails, THEN the panel SHALL keep showing the last good values and retry, and one failing source SHALL NOT affect other sources' fields.
6. Each idle screen SHALL declare a render mode: `mono` (1-bit, partial refresh, full refresh every N partials) or `gray4` (4-level gray, full refresh at an interval).
7. WHEN an idle upload is corrupt, THEN the panel SHALL answer NAK and keep the previous store intact.
8. IF an idle upload does not fit in the panel's free idle flash, THEN the plugin SHALL refuse it and say so.

### Requirement 4: Screen packages

**User Story:** As a screen author, I want a small, documented package format with an emulator, so that I can write and
test a screen without a panel.

#### Acceptance Criteria

1. A screen package SHALL be a folder or `.zip` containing `screen.json` and optionally assets and one `screen.py`, with an id of lowercase `[a-z0-9-]`.
2. WHEN a package is validated, THEN the system SHALL reject it unless it has an `active` section, an `idle` section, or both, and the active size equals 648x480.
3. WHEN a package is imported, THEN the system SHALL enforce size, entry-count, path, entry-type and compression-ratio limits before anything is installed, and SHALL install atomically.
4. WHEN a package contains `screen.py`, THEN the system SHALL keep it disabled until the user approves the exact author, version and sha256 of the code.
5. IF `screen.py` changes after approval, THEN the system SHALL revoke the approval and disable the screen.
6. WHEN `screen.py` runs, THEN the system SHALL run it in a separate process with an empty environment, a 2 second timeout and resource limits, and SHALL draw an error screen if it fails.
7. IF a package id collides with a built-in screen id, THEN the system SHALL refuse it.
8. WHEN no panel is attached, THEN the emulator SHALL render any screen or idle layout to a PNG.
9. Importing a zip or an https link SHALL be refused until Developer mode is on.

### Requirement 5: Built-in screens

**User Story:** As a new user, I want useful screens out of the box, so that the panel works right after setup.

#### Acceptance Criteria

1. The plugin SHALL ship four live screens: Telemetry, Thermals & Power, Big Clock and Now Playing.
2. The plugin SHALL ship idle screens: Clock & Weather (mono and 4-gray variants), Steam Stats and Minimal Clock.
3. All built-in screens SHALL be declarative and SHALL have golden image tests in the host emulator.
4. Code-tier screens SHALL NOT be accepted as built-ins.

### Requirement 6: Switching and settings

**User Story:** As a user, I want to pick which screens show and for how long, so that the panel fits my setup.

#### Acceptance Criteria

1. The user SHALL be able to pin one screen, or set a rotation list and an interval (5 to 86400 seconds) per mode.
2. WHEN a game launches and auto Now Playing is on, THEN the system SHALL switch to Now Playing and return to the previous screen when the game exits.
3. WHERE the in-game list is enabled, WHILE a game runs, the system SHALL use the in-game rotation, interval and pin instead of the normal ones, switching at most once per 3 seconds.
4. Each screen SHALL have a settings page generated from its `settings` schema and validated at render time.
5. IF the settings file is unreadable or invalid, THEN the system SHALL fall back to defaults, move the bad file aside, and show the error.
6. The system SHALL accept Wi-Fi, location and Steam account settings from the plugin and send them to the panel, and the Steam API key and Wi-Fi password SHALL never be read back into the UI.

### Requirement 7: Host data sources

**User Story:** As a screen author, I want a fixed set of bindable data, so that my screen needs no code to show system
and Steam information.

#### Acceptance Criteria

1. The host SHALL provide time, system (`/proc` and hwmon: load, temperature, clocks, fan, power), local Steam (current game, box art, playtime), optional Steam Web API, and Open-Meteo weather sources.
2. IF a source fails, THEN the host SHALL report the failure to the screen as data instead of stopping the render loop.
3. WHEN a value that the current screen displays changes, THEN the source registry SHALL notify the engine so that it renders at once instead of waiting for the next tick, and sources that own a displayed value SHALL be sampled faster than their normal interval.

### Requirement 8: Signed firmware updates

**User Story:** As a user, I want firmware updates over the same cable that I can trust, so that I never flash an
unverified image.

#### Acceptance Criteria

1. WHEN the plugin starts and then daily, THEN it SHALL look for a `fw-v*` release on GitHub with at most one API request per 24 hours, respecting rate limits.
2. WHEN a release is found, THEN the plugin SHALL download `manifest.json` and `manifest.sig` and verify the Ed25519 signature with the bundled public key before it reads the manifest, and SHALL abort on failure.
3. IF the manifest's version is not newer, its protocol version differs, or the plugin is older than `min_plugin_version`, THEN the plugin SHALL NOT offer the update.
4. The plugin SHALL show the changelog and install only after the user taps Install.
5. WHEN installing, THEN the plugin SHALL check the image's size and sha256 against the manifest, stream it into the panel's inactive OTA slot, and send no heartbeats or rectangles meanwhile.
6. WHEN the panel has received the image, THEN it SHALL verify the sha256 of what is in flash and the ESP image, then reboot into the new slot.
7. WHEN the host accepts the new firmware's HELLO, THEN the panel SHALL mark the image valid. IF no acknowledgement arrives within 180 seconds, THEN the bootloader SHALL roll back to the previous slot.
8. IF the transfer is interrupted, THEN the old firmware SHALL still boot, and the transfer SHALL resume when possible.

### Requirement 9: Release and quality gates

**User Story:** As a maintainer, I want every change tested and every release signed, so that the project stays
trustworthy.

#### Acceptance Criteria

1. WHEN a change is merged, THEN CI SHALL have run the `pytest`, `firmware` and `plugin` jobs green.
2. WHEN OTA code or the firmware updater changes, THEN CI SHALL fail if its coverage is under 90 percent.
3. WHEN a tag `fw-v*` is pushed, THEN CI SHALL build the `ee05` firmware, sign `manifest.json` with the repository secret, verify it against the plugin's bundled key, and publish the assets.
4. WHEN a tag `plugin-v*` is pushed, THEN CI SHALL refuse it unless it matches `VERSION` and `decky/package.json`, and SHALL publish the plugin zip.
5. IF a tracked file is larger than 2 MB, THEN CI SHALL fail.
6. WHEN docs name a repository path or link to another doc, THEN a test SHALL fail if the target does not exist.

### Requirement 10: v1 hardware done criteria

**User Story:** As the maintainer, I want a fixed list of hardware checks on a real Steam Machine, so that I know when
v1 is done.

#### Acceptance Criteria

1. The plugin SHALL install by sideloading the zip in Decky developer mode.
2. The plugin SHALL find the panel over USB and the handshake SHALL pass.
3. Four live screens and three idle screens SHALL render.
4. Switching between live and idle mode SHALL work in both directions.
5. A changed value SHALL repaint in under 1 second.
6. A third-party screen zip SHALL import and run.
7. A firmware OTA from a real GitHub release SHALL succeed.
8. A forced rollback SHALL recover.

Requirements 1 to 9 are verified with unit tests and the host PNG emulator. The maintainer verifies hardware on a real
Steam Machine.

### Out of scope for v1

- A plain-Linux daemon (the core package has no Decky imports so it stays possible).
- Feather boards and Bluetooth (they stay on the `v1-ble` tag of the original design).
- Installing firmware without the user's tap.
- Python (`screen.py`) idle screens, because the panel cannot run Python.
- 4-gray in live (active) mode.
