---
inclusion: always
---

# Product

Inkterface is an e-ink faceplate for the Steam Machine: a Decky Loader plugin plus custom firmware for the Seeed XIAO
ePaper Display Board EE05 (ESP32-S3) with a 5.83" 648x480 UC8179 panel. One USB cable carries data and power.
Readme: #[[file:readme.md]]

## What it does

- **Live screens** (machine on): the plugin renders each frame on the host, diffs it against the last one and sends only
  the changed rectangles. Built in: Telemetry, Thermals & Power, Big Clock, Now Playing.
- **Standby screens** (machine off or asleep): the panel draws a stored layout by itself and pulls data over Wi-Fi.
  Built in: Clock & Weather (mono and 4-gray), Minimal Clock, Steam Stats.
- **Screens are data**: a folder or zip with `screen.json` and optionally one `screen.py`. They render in a host PNG
  emulator with no panel, and install from a signed Screen Store or from a zip in developer mode.
- **Signed firmware updates**: the plugin verifies an Ed25519 signature on a GitHub release and streams the image over USB.
  The user always taps Install. An unconfirmed image rolls back.

## Who it is for

Steam Machine and Steam Deck owners who build the panel (printed case, no soldering), and community authors who write
screens. Hardware and beta status: users flash the panel once from a computer, then update through the plugin.

## Product rules

- The panel only talks USB serial. No Bluetooth. Feather boards are not supported by this stack.
- Never install firmware without the user's tap. Never run code screens without the user approving the exact code.
- A changed value must repaint in under 1 second (median measured on a real panel is about 0.7 s).
- Beta honesty: docs state known limits (see #[[file:CHANGELOG.md]]) instead of hiding them.
- Community screens go to the separate screens repository, not this one. Only built-in screens live in `decky/screens/`.
