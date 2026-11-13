---
inclusion: auto
name: device-safety
description: Rules for touching real hardware, the panel on a dev machine or a Steam Machine. Use when running bench tools, flashing, OTA, sleep or soak tests, or driving the Steam UI on a device.
---

# Device safety

Automated tests use the emulator. When a step needs real hardware, these rules apply.

## The panel

- The panel is identified by USB vendor id `303a` (Espressif), never by "first ttyACM". On a Steam Machine `/dev/ttyACM0`
  and `/dev/ttyACM1` can be game controllers: never open them.
- Bench tools in `decky/tools/` (`send_test.py`, `edge_test.py`, `hw_e2e.py`, `sleep_test.py`, `idle_test.py`) open the
  real port. Run them only on a machine you own.
- Opening the port can reset the chip if DTR and RTS change one at a time. Use `usb_link.open_port` (drops both lines
  in one ioctl) and do not open the port with other tools in between.
- When several people or agents share one panel, serialize access: wrap every command that opens it in an exclusive
  `flock`, and re-flash your own firmware build before measuring after someone else used it. A result from a window
  where another run also had the panel does not count.

## Firmware

- Updates are signed. The plugin verifies an Ed25519 signature over the manifest and a sha256 over the image before it
  streams anything, and it never installs without the user's tap. Do not add a path that skips the check.
- Do not send heartbeats or rectangles while an OTA install runs. The new image becomes valid only after the host
  accepts its HELLO; without that it rolls back after about 180 s. See #[[file:docs/ota.md]]
- Back up the stock firmware before the first flash: #[[file:docs/flashing.md]]

## A Steam Machine in use

Before any step that opens a debugging tunnel or sends input (key presses, clicks, navigation):

1. Check the foreground **read-only** first. The device must be marked free by its user, no game process may be
   running, no app may be in front, and the plugin's own page must be the page in front.
2. Stop if any check fails or is in doubt. Never send input while a game or any non-plugin page is in front.
3. Call only the plugin backend. Do not touch other plugins, Steam settings or accounts.
4. A screenshot taken while a game is in front captures the game and the account name and avatar. Review every
   screenshot before it goes into the repository, and blank any Wi-Fi name or personal text.
