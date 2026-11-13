<h1 align="center"><img src="assets/valve/icon256.png" alt="" width="96" height="96" align="middle"> <img src="assets/valve/Library_Logo.png" alt="Inkterface" width="320" align="middle"></h1>

<p align="center"><b>An e-ink faceplate for the Steam Machine.</b><br>
Live stats, what you are playing and a clock on a 5.83" panel, over one USB cable.</p>

<p align="center">
<a href="https://www.printables.com/model/1897091-inkterface-e-ink-faceplate-for-the-steam-machine-s"><img alt="Print it on Printables" src="https://img.shields.io/badge/Print%20it%20on-Printables-FA6831?logo=printables&logoColor=white&style=for-the-badge"></a>
<a href="https://ko-fi.com/inkterface"><img alt="Support on Ko-fi" src="https://img.shields.io/badge/Support%20on-Ko--fi-0B6FBF?logo=kofi&logoColor=white&style=for-the-badge"></a>
<br>
<a href="https://github.com/FliesWithWind/inkterface/actions/workflows/ci.yml"><img alt="CI status" src="https://img.shields.io/github/actions/workflow/status/FliesWithWind/inkterface/ci.yml?branch=main&label=CI"></a>
<a href="https://github.com/FliesWithWind/inkterface/releases"><img alt="Latest firmware release" src="https://img.shields.io/github/v/release/FliesWithWind/inkterface?include_prereleases&filter=Firmware*&display_name=release&label=&color=blue"></a>
<a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/github/license/FliesWithWind/inkterface"></a>
</p>

<p align="center">
<img src="docs/img/build/steam-machine-front.jpg" alt="The e-ink panel on the front of a Steam Machine, with a controller on top" width="285">
<img src="docs/img/build/live-telemetry.webp" alt="The panel updating live with system telemetry" width="348">
<br><sub>The panel on a Steam Machine, updating live over one USB cable.</sub>
</p>

Inkterface is a [Decky Loader](https://decky.xyz) plugin plus custom firmware for the Seeed XIAO ePaper Display Board EE05.
A 648x480 e-paper panel magnets onto the front of the Steam Machine. One USB cable, no battery, no soldering.
Based on [Valve's original Inkterface](https://gitlab.steamos.cloud/SteamHardware/SteamMachine/inkterface).

> [!WARNING]
> **Beta.** Inkterface is beta software and hardware. Expect bugs, rough edges and changes between releases.
>
> **Use at your own risk.** This is a hobby project, provided as is, with no warranty of any kind (see [LICENSE](LICENSE)). The author is not responsible for any damage to your Steam Machine, the display, the EE05 board, USB ports or any other hardware, or for any data loss, from building, flashing or using it.

[Build one](#build-one) · [Features](#features) · [Screenshots](#screenshots) · [Hardware](#hardware) · [Install](#install) · [Quick start](#quick-start) · [Screens](#screens-and-the-screen-store) · [Docs](#docs) · [Contributing](#contributing) · [Security](#security) · [Support](#support) · [License](#license-and-credits)

## Build one

The USB cable runs under the Steam Machine, out of sight. No battery and no soldering.

1. **Buy the parts.** Five parts and one cable: see the [parts list](#parts-list).
2. **[Print the case on Printables](https://www.printables.com/model/1897091-inkterface-e-ink-faceplate-for-the-steam-machine-s)** (the EE05 remix of Valve's case).
3. **Flash the panel and install the plugin.** [Flash the firmware](docs/flashing.md) once, then [install the plugin](#install).

**Want a DIY kit?** I may put together kits if there is enough interest. [Request a kit](https://github.com/FliesWithWind/inkterface/issues/new?template=kit-request.yml) so I can see the demand.

## Features

* **Live Screens.** The plugin draws screens on the Steam Machine and sends only the changed regions over USB.
  A value change repaints in well under a second. Built in: Telemetry, Thermals & Power, Big Clock, Now Playing.
* **Standby Screens.** When the machine is off or asleep, the panel runs its own screens: clock and weather,
  Steam stats, a minimal clock. Wi-Fi, location and Steam account are set in the plugin.
* **In-game rotation.** An optional separate list, timing and pin while a game runs.
* **Screen Store.** Install community screens from the plugin. The catalog is signed and every download is checked
  against it. Code screens stay disabled until you approve the exact code.
* **Signed OTA firmware.** The plugin finds new firmware on GitHub, verifies an Ed25519 signature and flashes it
  over the same cable. A new image that does not confirm itself rolls back.
* **4-gray waveform.** A custom UC8179 waveform for gray text and images ([docs/gray-waveform.md](docs/gray-waveform.md)).
* **Easy to extend.** A screen is a small package (JSON, optionally one Python file) with an emulator, so you can
  write and test one without a panel.

## Screenshots

### Plugin pages in Gaming Mode

Live Screens, Standby Screens, Screen Store and the In a game list.

<p align="center">
<img src="docs/ui/device/gm-live.png" alt="Live Screens page with the panel preview and the rotation list" width="410">
<img src="docs/ui/device/gm-standby.png" alt="Standby Screens page with the panel status and four panel screens" width="410">
<img src="docs/ui/device/gm-store.png" alt="Screen Store with three community screens" width="410">
<img src="docs/ui/device/gm-live-ingame.png" alt="Live Screens with the In a game tab and its own rotation" width="410">
</p>

### The build

<p align="center"><img src="docs/img/build/build-back.jpg" alt="Back of the build: the EE05 in the remixed board plate, four magnets and the USB cable routed out at a corner" width="520"><br>
<sub>The EE05 in the board plate, 4 magnets, USB cable routed out under the Steam Machine.</sub></p>

## Hardware

Parts, assembly and the case: [docs/hardware.md](docs/hardware.md). The parts and links come from [`hardware/bom.yaml`](hardware/bom.yaml).

### Parts list

<!-- gen:bom -->
| Qty | Part | Spec | Where to buy | AliExpress | Notes |
|----:|------|------|--------------|------------|-------|
| 1 | XIAO ePaper Display Board (ESP32-S3) EE05 | Seeed EE05, ESP32-S3, 24-pin FPC connector for the panel, USB-C | [Seeed](https://www.seeedstudio.com/XIAO-ePaper-Display-Board-EE05-p-6755.html?sensecap_affiliate=dlPNUD2&referring_service=link) / [OpenELAB](https://openelab.io/products/seeed-studio-xiao-epaper-board-ee05) | [AliExpress](https://s.click.aliexpress.com/e/_c43WvPXd) | Runs the Inkterface firmware. USB powered. |
| 1 | 5.83" monochrome ePaper display | 648x480 pixels, UC8179 controller, FPC cable | [Seeed](https://www.seeedstudio.com/5-83-Monochrome-ePaper-Display-with-648x480-Pixels-p-5785.html?sensecap_affiliate=dlPNUD2&referring_service=link) / [OpenELAB](https://openelab.io/products/goodisplay-5-83inch-e-paper) | [AliExpress](https://s.click.aliexpress.com/e/_c3zLNr7N) | Plugs straight into the EE05 FPC connector. No soldering. The flex cable is fragile. |
| 4 | Magnets | 10 x 10 x 4 mm | Amazon [PL](https://www.amazon.pl/dp/B075P5M649?tag=flieswithwind-21) · [DE](https://www.amazon.de/dp/B075P5M649?tag=flieswithwi04-21) · [FR](https://www.amazon.fr/dp/B075P5M649?tag=flieswithwi0f-21) · [ES](https://www.amazon.es/dp/B075P5M649?tag=flieswithwi0a-21) · [IT](https://www.amazon.it/dp/B075P5M649?tag=flieswithwi0d-21) · [UK](https://www.amazon.co.uk/dp/B075P5M649?tag=flieswithwi07-21) · [US](https://www.amazon.com/dp/B0H3TFLG55?tag=flieswithwind-20) | [AliExpress](https://s.click.aliexpress.com/e/_c3g72c8j) | One per corner of the midplates. Orientation does not matter. |
| 13 | M2.5x5 flat head hex socket screw (304 stainless) | M2.5 thread, 5 mm long, flat head, hex socket | Amazon [PL](https://www.amazon.pl/dp/B0G6ZCMST1?tag=flieswithwind-21) · [DE](https://www.amazon.de/dp/B0G6ZCMST1?tag=flieswithwi04-21) · [FR](https://www.amazon.fr/dp/B0G6ZCMST1?tag=flieswithwi0f-21) · [ES](https://www.amazon.es/dp/B0G6ZCMST1?tag=flieswithwi0a-21) · [IT](https://www.amazon.it/dp/B0G6ZCMST1?tag=flieswithwi0d-21) · [UK](https://www.amazon.co.uk/dp/B0G6ZCMST1?tag=flieswithwi07-21) · [US](https://www.amazon.com/dp/B0G6ZCMST1?tag=flieswithwind-20) | [AliExpress](https://s.click.aliexpress.com/e/_c2Izsl8P) | Threaded straight into the printed plastic. |
| 1 | USB-C to USB 2.0 cable, 0.5 m | USB-C plug to USB-A 2.0 plug, data and power | Amazon [PL](https://www.amazon.pl/dp/B07NSQ56DP?tag=flieswithwind-21) · [DE](https://www.amazon.de/dp/B07NSQ56DP?tag=flieswithwi04-21) · [FR](https://www.amazon.fr/dp/B07NSQ56DP?tag=flieswithwi0f-21) · [ES](https://www.amazon.es/dp/B07NSQ56DP?tag=flieswithwi0a-21) · [IT](https://www.amazon.it/dp/B07NSQ56DP?tag=flieswithwi0d-21) · [UK](https://www.amazon.co.uk/dp/B07NSQ56DP?tag=flieswithwi07-21) · [US](https://www.amazon.com/dp/B07NSQ56DP?tag=flieswithwind-20) | [AliExpress](https://s.click.aliexpress.com/e/_c4noGSGL) | From the EE05 to a USB port on the Steam Machine. Must carry data, not charge-only. |
<!-- /gen:bom -->

<!-- gen:affiliate -->
**Affiliate links.** Some store links in the parts list are affiliate links: if you buy through them, the project may earn a small commission at no extra cost to you. As an Amazon Associate I earn from qualifying purchases.
<!-- /gen:affiliate -->

### Printed case

<!-- gen:printed -->
The enclosure is a remix of [Valve's Inkterface case](https://gitlab.steamos.cloud/SteamHardware/SteamMachine/inkterface), modified for the EE05. Its print files are on [Printables](https://www.printables.com/model/1897091-inkterface-e-ink-faceplate-for-the-steam-machine-s) under CC BY-NC-SA 4.0. Valve's original design is MIT (Copyright (c) 2026 Valve Software), see THIRD_PARTY_NOTICES.md and Valve's GitLab repository.
<!-- /gen:printed -->

## Install

You need Decky Loader on the Steam Machine and a built panel.

1. **Flash the panel once** from a computer with [PlatformIO](https://platformio.org). Back up the stock firmware
   first. Steps and restore: [docs/flashing.md](docs/flashing.md). In short:

   ```sh
   cd firmware
   cp -n src/ee05/idle_config.h.example src/ee05/idle_config.h
   pio run -e ee05 -t upload --upload-port <PORT>
   ```

2. **Install the plugin.** Download `inkterface-plugin-<version>.zip` from the
   [releases](https://github.com/FliesWithWind/inkterface/releases) (tag `plugin-v*`). In Decky settings turn on
   **Developer mode**, then **Install plugin from ZIP file**.
3. **Plug the panel into a USB port** of the Steam Machine. The Inkterface page shows
   `Connected - firmware <version>` once the handshake passes.

After the first flash, firmware updates come through the plugin (Firmware page, Install button). To update the
plugin, install the newer zip the same way.

## Quick start

1. Open **Inkterface** in the Decky quick-access menu for the live preview and a **Pin screen** dropdown.
2. On **Live Screens**, enable screens, set their order and the seconds per screen.
3. On **Standby Screens**, set Wi-Fi, location and Steam account for when the machine is off or asleep.
4. Browse the **Screen Store** for community screens.

Serial permission, sleep and wake: [docs/using.md](docs/using.md). Problems: [docs/troubleshooting.md](docs/troubleshooting.md).

## Screens and the Screen Store

A screen is a folder with a `screen.json` and optionally one `screen.py`.

* Community screens live in [FliesWithWind/inkterface-screens](https://github.com/FliesWithWind/inkterface-screens).
  Open a pull request there. CI validates the screen and publishes the signed catalog the Screen Store reads
  ([docs/screen-catalog.md](docs/screen-catalog.md)).
* Format: [docs/screen-format.md](docs/screen-format.md). Packaging, approval and security:
  [docs/screen-packages.md](docs/screen-packages.md). Data you can bind to: [docs/data-sources.md](docs/data-sources.md).
* Try a screen with no panel and no Steam Machine, using the emulator (from the repo root):

  ```sh
  cd decky/py_modules
  python -m inkterface_core.screens.emulate ../examples/hello-screen --out hello.png
  python -m inkterface_core.screens.emulate --all ../screens --out out/    # all built-ins
  ```

## Docs

| Doc | What it covers |
|-----|----------------|
| [docs/README.md](docs/README.md) | Index of all docs |
| [docs/using.md](docs/using.md) | Rotation, pin, in-game list, standby screens, sleep and wake |
| [docs/hardware.md](docs/hardware.md) | Parts, case and assembly |
| [docs/ota.md](docs/ota.md) | Signed firmware updates and rollback |
| [CHANGELOG.md](CHANGELOG.md) | What changed and what is limited |

## Contributing

Code changes are welcome. Every change ships with tests. Setup, test commands and coding standards:
[CONTRIBUTING.md](CONTRIBUTING.md). Maintainers: [docs/RELEASING.md](docs/RELEASING.md). To run the Python tests:

```sh
decky/scripts/vendor.sh    # once: fetches the pinned, sha256-checked Pillow
python3 -m pytest decky/tests -q
```

New screens go to the [screens repository](https://github.com/FliesWithWind/inkterface-screens), not here.

## Security

* Firmware releases and the Screen Store catalog are signed with Ed25519 (separate keys). The plugin checks the
  signature before it reads a manifest and refuses older versions. Details: [docs/ota.md](docs/ota.md).
* The panel does not verify signatures, and Wi-Fi and Steam settings sit unencrypted in its flash.
  Anyone with physical USB access can flash it.
* Code screens run Python with your user's permissions and are not sandboxed. The plugin keeps them disabled until
  you approve the exact code. Only approve code you trust.
* **Report a vulnerability** with GitHub's private reporting: the Security tab, then **Report a vulnerability**.
  Full model and limits: [SECURITY.md](SECURITY.md).

<!-- gen:support -->
## Support

Inkterface is free and open source. If it is useful to you, you can support its development on [Ko-fi](https://ko-fi.com/inkterface).

[![ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/W7W72AIPDL)
<!-- /gen:support -->

## License and credits

* **Code:** MIT, see [LICENSE](LICENSE). Copyright (c) 2026 Valve Software for the original work, and Copyright (c) 2026
  FliesWithWind for the USB + Decky stack.
* **Case:** the EE05 remix is on Printables under CC BY-NC-SA 4.0 ([docs/hardware.md](docs/hardware.md#enclosure)).
* **Valve:** the enclosure design, the icon and wordmark ([assets/valve/README.md](assets/valve/README.md)) and the
  PlatformIO layout come from [Valve's Inkterface](https://gitlab.steamos.cloud/SteamHardware/SteamMachine/inkterface)
  (MIT, Copyright (c) 2026 Valve Software). Valve's Bluetooth and Qt app and Feather firmware are not used here;
  [docs/references.md](docs/references.md) links them.
* **Third party:** Pillow (fetched at build time) and IBM Plex Mono, modified and renamed Inkterface Mono, under the
  SIL OFL. Full list: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
