# Valve artwork

Graphics from Valve's original Inkterface that this project reuses. Everything in this folder is Valve's work and
byte-identical to the file in Valve's repository.

Source: [SteamHardware/SteamMachine/inkterface](https://gitlab.steamos.cloud/SteamHardware/SteamMachine/inkterface),
commit [`477563c`](https://gitlab.steamos.cloud/SteamHardware/SteamMachine/inkterface/-/tree/477563c).

| File | What it is | Original in Valve's repo | Used by |
|------|------------|--------------------------|---------|
| `icon.png` | 48x48 pixel-art Inkterface icon: a panel showing "INK". The native pixel grid | [resources/icon.png](https://gitlab.steamos.cloud/SteamHardware/SteamMachine/inkterface/-/blob/477563c/resources/icon.png) | The Decky quick-access icon |
| `icon256.png` | The same icon at 256x256 (a nearest-neighbour enlargement of `icon.png`, cells of 5 or 6 px) | [resources/icon256.png](https://gitlab.steamos.cloud/SteamHardware/SteamMachine/inkterface/-/blob/477563c/resources/icon256.png) | `readme.md` header, shown at 256 px |
| `Library_Logo.png` | 1280x229 "INKTERFACE" wordmark, transparent background | [design/store-assets/Library_Logo.png](https://gitlab.steamos.cloud/SteamHardware/SteamMachine/inkterface/-/blob/477563c/design/store-assets/Library_Logo.png) | `readme.md` header |

The rest of Valve's artwork (Steam store capsules, the Machine_Status images, the boot-splash portrait, the faceplate drawing)
is not kept: nothing here uses it. It stays at the commit above.

## Using the icon without blurring it

`icon.png` is the real pixel grid (48x48). Scale it only by whole numbers (2x = 96, 3x = 144, ...) and with
nearest-neighbour: in CSS `image-rendering: pixelated`. The Decky plugin does that. `icon256.png` is meant to be
shown at its own size; GitHub's readme cannot set `image-rendering`, so shrinking or stretching it there would blur it.

## Licence

Valve's repository states one licence for the whole project: MIT, "Copyright (c) 2026 Valve Software"
([LICENSE at `477563c`](https://gitlab.steamos.cloud/SteamHardware/SteamMachine/inkterface/-/blob/477563c/LICENSE)).
It has no separate statement for the artwork, so the files are covered by that MIT licence and nothing more is
promised. That licence covers copyright, not trademarks: "Steam" and "Valve" remain Valve's marks. Keep this notice with the files.
