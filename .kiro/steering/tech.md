---
inclusion: always
---

# Tech stack

## Host (Decky plugin, `decky/`)

- **Python core** `decky/py_modules/inkterface_core/`: standard library plus a vendored, sha256-pinned Pillow. No Decky
  imports in the core (only `decky/main.py` touches Decky). Runs on Decky's bundled CPython 3.14.
- No `termios`, `tty`, `pty` and no pyserial: Decky's Python bundle lacks them. Serial I/O goes through
  `decky/py_modules/inkterface_core/serialport.py` (`os`, `fcntl`, `select`). No `zoneinfo`: use `inkterface_core/tz.py`.
- New stdlib imports are checked against `decky/tests/decky_3.3.1_stdlib.txt` by `decky/tests/test_bundle_imports.py`.
- **Frontend** `decky/src/`: TypeScript, React 18, `@decky/ui`, `@decky/api`; tests with vitest and Testing Library; build with rollup.
- pnpm is not installed globally: run it as `npx pnpm@9 <script>` from `decky/`.
- Pillow and certifi are not in git: `decky/scripts/vendor.sh` fetches them (see #[[file:decky/vendor-requirements.txt]]).

## Firmware (`firmware/`)

- PlatformIO, Arduino framework on `espressif32@7.1.3`, one product environment `ee05`. ArduinoJson 7.4.3.
- Bare-metal UC8179 driver (`firmware/src/ee05/uc8179_fast.h`) with a single-phase fast partial refresh. No GxEPD2.
- Dual OTA slots (`firmware/partitions.csv`) with app rollback enabled.
- C++17, `.clang-format` (LLVM base, 4 spaces, 100 columns). Host-testable logic lives in files that `firmware/host_test/` compiles with g++.

## Protocol and trust

- USB CDC serial, framed binary protocol v1 (magic `A5 5A`, CRC32, payload up to 4096): #[[file:docs/usb-protocol-v1.md]]
- The panel is found by USB vendor id `303a`, never by "first ttyACM".
- Ed25519 signatures: firmware manifests and the Screen Store catalog use separate keys. Release tooling is in `tools/release/`.

## CI (`.github/workflows/ci.yml`)

Three jobs must be green: `pytest` (decky/tests with a 90% coverage gate on the OTA code), `firmware` (`pio run -e ee05`
plus g++ sanitizer host tests) and `plugin` (typecheck, vitest, build, zip). Commands: #[[file:CONTRIBUTING.md]]

## Dependencies

Do not add third-party Python dependencies to the core. A new dependency anywhere needs an entry in
`THIRD_PARTY_NOTICES.md` if it ships in a release artifact.
