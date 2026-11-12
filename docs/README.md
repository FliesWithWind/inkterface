# Inkterface docs

Start with the [project readme](../readme.md). Contributing: [CONTRIBUTING.md](../CONTRIBUTING.md).
Release notes: [CHANGELOG.md](../CHANGELOG.md). Security: [SECURITY.md](../SECURITY.md). Licences: [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).

## Using and extending the plugin

| Doc | What it covers |
|-----|----------------|
| [using.md](using.md) | Serial permission, rotation, pin, in-game list, standby screens, sleep and wake, firmware updates |
| [flashing.md](flashing.md) | First-time firmware flash over USB, with backup and restore |
| [troubleshooting.md](troubleshooting.md) | `CERTIFICATE_VERIFY_FAILED` on the Steam Machine, serial permission, panel not found |
| [hardware.md](hardware.md) | Bill of materials (generated from `hardware/bom.yaml`), enclosure credit, assembly |
| [screen-format.md](screen-format.md) | `screen.json` reference, widgets, settings, idle section, emulator usage |
| [screen-packages.md](screen-packages.md) | Write, test, package, share and install community screens; approval and security model |
| [screen-catalog.md](screen-catalog.md) | The Screen Store: community screen repo, signed index, trust model, plugin callables, maintainer setup |
| [data-sources.md](data-sources.md) | Data you can bind to (time, system, Steam, weather) and how sources behave |
| [plugin-ui.md](plugin-ui.md) | Decky plugin backend callables, events and every UI page |
| [ota.md](ota.md) | Signed firmware updates over USB: flow, trust model, maintainer runbook, rollback |
| [RELEASING.md](RELEASING.md) | Maintainer steps for a release: keypair, secret, public key, PR, tags, verifying the signed release |
| Photos: [live telemetry](img/build/live-telemetry.webp), [front](img/build/steam-machine-front.jpg), [back](img/build/build-back.jpg); Gaming Mode captures: [Live Screens](ui/device/gm-live.png), [In a game](ui/device/gm-live-ingame.png), [Standby Screens](ui/device/gm-standby.png), [Screen Store](ui/device/gm-store.png) | Images for the [project readme](../readme.md) and [hardware.md](hardware.md) |

## How it works

| Doc | What it covers |
|-----|----------------|
| [engine.md](engine.md) | Host runtime: render, diff, send loop, scheduler, settings |
| [usb-protocol-v1.md](usb-protocol-v1.md) | USB wire protocol, OTA frames, hardware test procedure and flash backup/restore |
| [idle-mode.md](idle-mode.md) | Panel idle mode: host-pushed declarative layouts, flash store, Wi-Fi sources, upload protocol |
| [partial-refresh.md](partial-refresh.md) | EE05 fast partial refresh and ghosting policy |
| [ghost-clear.md](ghost-clear.md) | The full refresh that clears ghosting: every `ghost_clear_minutes` (default 10), what to look at on the glass |
| [uc8179-tables.md](uc8179-tables.md) | UC8179 init registers and LUTs, value by value against the panel datasheet, with our own tuning marked |
| [gray-waveform.md](gray-waveform.md) | Provenance of the 4-gray waveform: where the values come from and why no third-party bytes are in the repository |
| [decky-runtime.md](decky-runtime.md) | Why there is no pyserial, what Decky's Python bundle contains, how Pillow is vendored |
| [../decky/README.md](../decky/README.md) | Plugin layout and test commands |

## Hardware references

| File | What it is |
|------|------------|
| [references.md](references.md) | Datasheets (vendor links), and where Valve's assembly video and PDFs, wiring diagrams, KiCad boards and Qt app now live |
| [../assets/valve/README.md](../assets/valve/README.md) | Valve's artwork this project reuses: icon and wordmark, with licence |
