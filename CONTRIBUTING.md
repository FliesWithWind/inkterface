# Contributing to Inkterface

Thanks for helping. The short version: small changes, every change ships with tests, CI stays green.

## Layout

| Path | What |
|------|------|
| `decky/` | Decky Loader plugin: Python core in `decky/py_modules/inkterface_core/`, React frontend in `decky/src/`, built-in screens in `decky/screens/` |
| `firmware/` | PlatformIO project. `src/ee05/` is the firmware, `src/pbench/` the partial-refresh bench ([docs/partial-refresh.md](docs/partial-refresh.md)) |
| `tools/release/` | Firmware signing and verification scripts |
| `docs/` | Documentation, indexed in [docs/README.md](docs/README.md) |
| `assets/valve/` | Valve's artwork we reuse, see its README |

## Dev setup

Needs Python 3.14 (what Decky ships; the vendored Pillow is the cp314 build) with `pip`, `g++` (firmware host
tests are compiled and run by pytest), Node 20, and PlatformIO. `pnpm` is not required: use
`npx pnpm@9`.

```sh
pip install pytest pyserial cryptography coverage   # pyserial and cryptography: opt-in cross-check tests only
cd decky && npx pnpm@9 install --frozen-lockfile
decky/scripts/vendor.sh   # from the repo root: the pinned, sha256-checked Pillow and certifi (they are not in git; no-op when current)
```

Pillow and certifi (the CA bundle for TLS, `inkterface_core/tls.py`) are fetched, not committed: `decky/vendor-requirements.txt` pins the version and the wheel's sha256,
`decky/scripts/vendor.sh` unpacks it into `decky/py_modules/` (gitignored), and `make_zip.py` refuses to build
without it. To update Pillow, change the pin in that file (hash from the PyPI JSON page), run the script and the tests.

## Test commands

All of these must pass before you open a PR. CI (`.github/workflows/ci.yml`) runs the same commands.

| Check | Command |
|-------|---------|
| Python (protocol, emulator, engine, plugin backend, OTA, firmware host tests, docs path check) | `python3 -m pytest decky/tests -q` (from the repo root) |
| Frontend | `cd decky && npx pnpm@9 test` (vitest) |
| Types | `cd decky && npx pnpm@9 typecheck` (tsc) |
| Frontend build | `cd decky && npx pnpm@9 build` (writes `decky/dist/index.js`) |
| Firmware build | `cp -n firmware/src/ee05/idle_config.h.example firmware/src/ee05/idle_config.h && cd firmware && pio run -e ee05` |
| Plugin zip | `decky/scripts/build_idle_render.sh && python3 decky/scripts/make_zip.py out/inkterface-decky.zip` (the script builds the static idle-preview renderer the zip ships; needs g++) |

CI also gates coverage on the OTA code and the firmware updater at 90%:

```sh
coverage run --branch --include='decky/py_modules/inkterface_core/ota/*,decky/py_modules/inkterface_core/firmware.py' \
  -m pytest -q decky/tests && coverage report --fail-under=90 -m
```

You can run the plugin core without a panel: `python3 decky/tools/run_engine.py --emulator out/`
renders to PNGs. Hardware bench tools (`decky/tools/send_test.py`, `edge_test.py`) open the real
panel, so use them only on a machine you own and never on `/dev/ttyACM0` or `ttyACM1`, which are
game controllers on a Steam Machine. The panel is identified by USB VID `303a`.

## The rule: every change ships with tests

* New logic gets tests for the normal path and its edge cases (empty, malformed, boundary,
  failure). Bug fixes get a test that fails without the fix.
* No hardware? Use the emulator (`inkterface_core/emulator.py`), fakes, or pty pairs, and say in
  the PR what still needs a real panel.
* Docs that name a file or link to another doc are checked by
  `decky/tests/test_repo_paths.py`. If you move or delete a path, fix the docs and workflows that
  mention it, or that test fails.

## No new large binaries

Do not commit files over 2 MB (videos, datasheets, 3D model exports, firmware images, wheels). Git history is forever and every
clone pays for it. Host big media elsewhere (a GitHub release asset, a link) and reference it. No file is over the limit today, so
`tools/large-files-allowlist.txt` is empty; do not add to it. `python3 tools/check_file_sizes.py` checks the rule, CI runs
it, and so does `decky/tests/test_file_sizes.py`.

## Keeping the suite lean

Add a test per behaviour, not per input: one parametrize case per distinct branch, boundary or type.
Put the full input list where the rule lives (validator, parser, store) and give the layer above it
one or two cases for the error shape. Never remove a regression or security test.

## Coding standards

**Python core (`decky/py_modules/inkterface_core/`).** Standard library plus the vendored Pillow
only. No new third-party dependencies. The code must import inside Decky's PyInstaller bundle,
which lacks some stdlib modules:

* No `termios`, `tty`, `pty` (so no pyserial). Serial I/O goes through `serialport.py`
  (`os`, `fcntl`, `select`).
* No `zoneinfo`. Get named zones from `inkterface_core.tz.get_zone(name)`: it uses `zoneinfo` when
  present and otherwise reads the system's `/usr/share/zoneinfo` files. With no tz data at all the
  time source falls back to the system zone and sets a `warning`.
* Check any new stdlib import against `decky/tests/decky_3.3.1_stdlib.txt`.
  `decky/tests/test_bundle_imports.py` enforces it: it imports every core module and runs the whole
  plugin (screens, engine, packages, firmware) under an import hook that refuses anything else.
  An optional import needs a `try/except ImportError` fallback, a both-paths test, and an entry in
  that test's `GUARDED` set.

Background: [docs/decky-runtime.md](docs/decky-runtime.md). No Decky imports in `inkterface_core` (only
`decky/main.py` touches Decky).

**Frontend.** TypeScript + React with `@decky/ui`. Components stay mocked-testable (see
`decky/test/mocks/`). Show backend errors in the UI, do not swallow them.

**Firmware C++ (`firmware/src/ee05/`).** Follow `.clang-format` (LLVM base, 4 spaces, Linux
braces, 100 columns): `clang-format -i <file>` before committing. Host-testable logic goes in
files the host tests compile (`firmware/host_test/`).

**Docs.** Keep claims checkable. Link to code and other docs instead of copying them.

## Contributing a screen

**Community screens go to the catalog repo**, not here: `github.com/FliesWithWind/inkterface-screens` (screens live in `screens/<id>/`; its README
explains how to make, preview and submit one; every screen is contributed under MIT; code screens get extra review). Users then install them from the
plugin's Screen Store page. See [docs/screen-catalog.md](docs/screen-catalog.md).

To write one (the same steps, locally):

1. Read [docs/screen-format.md](docs/screen-format.md) and [docs/screen-packages.md](docs/screen-packages.md).
2. Create `my-screen/` with `screen.json` (folder name equals `id`) and `sample-data.json`. See
   `decky/examples/` for a declarative and a code (`screen.py`) example.
3. Render it with no panel:
   `cd decky/py_modules && python -m inkterface_core.screens.emulate ../examples/hello-screen --out out.png`
   (add `--allow-code` for a code screen).
4. Try it on a device: `python3 decky/examples/make_package.py my-screen`, then import the zip with **Settings, Developer mode** on.
5. Open the pull request on the catalog repo.

**Built-in screens** (this repo) are only the few that ship with the plugin: put the folder in `decky/screens/` and add a golden image test under
`decky/tests/screens/` (refresh with `pytest decky/tests/screens --update-goldens`, then review the PNG diff). Code-tier screens are not accepted
as built-ins. Changes to the catalog client or the Screen Store page go here (tests: `decky/tests/catalog/`, `decky/test/StorePage.test.tsx`).

## Releases

Maintainers cut releases by pushing a tag. The workflows run the full tests first.

* **Firmware:** tag `fw-v<semver>` (for example `fw-v1.2.0`; a `-beta.1` or `-rc.1` suffix is a pre-release).
  `firmware-release.yml` builds `ee05`, signs `manifest.json` with the repo secret
  `INKTERFACE_SIGNING_KEY`, verifies it against the key bundled in the plugin, and publishes the
  assets. Raise `firmware/min_plugin_version.txt` when firmware needs newer plugin behaviour.
  Key setup and rotation: [docs/ota.md](docs/ota.md).
* **Plugin:** the release version lives in the `VERSION` file. Set it and `version` in `decky/package.json`
  to the same value (`decky/tests/test_version.py` fails if they differ), then tag `plugin-v<same version>`.
  `plugin-release.yml` refuses a tag that disagrees with `VERSION` or `package.json` and publishes
  `inkterface-plugin-<version>.zip`. The firmware tag `fw-v<same version>` is checked against `VERSION` too.
