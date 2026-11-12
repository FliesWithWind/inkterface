# Inkterface Decky plugin

Decky Loader plugin that drives the EE05 e-paper panel over USB. How the plugin is wired:
[`docs/plugin-ui.md`](../docs/plugin-ui.md). Wire protocol:
[`docs/usb-protocol-v1.md`](../docs/usb-protocol-v1.md). Why no pyserial and what the Decky
bundle contains: [`docs/decky-runtime.md`](../docs/decky-runtime.md).

## Layout

```
main.py                         Decky entry point (thin async wrapper around PluginCore)
src/                            React frontend (@decky/ui): QuickPanel, pages/, steam.ts (now-playing). docs/plugin-ui.md
test/                           frontend tests (vitest + @testing-library/react, mocks of @decky/api and @decky/ui)
scripts/make_zip.py             builds out/inkterface-decky.zip (after `npx pnpm@9 build` and scripts/build_idle_render.sh)
scripts/build_idle_render.sh    static x86_64 build of the firmware's idle renderer -> bin/idle_render_host (idle preview; SteamOS has no compiler)
py_modules/inkterface_core/     stdlib-only core, importable without Decky
    serialport.py                 raw tty via fcntl/select (Decky's bundle has no termios)
    usb_link.py                   panel discovery (USB VID 303a via sysfs), open_port, Link, send_acked
    proto.py diff.py emulator.py  frame codec, dirty rects, firmware-equivalent emulator
    frame.py serial_ports.py      Pillow test frame, ttyACM/ttyUSB listing
    engine/                       host runtime: settings, scheduler, render->diff->send loop (docs/engine.md)
    plugin_core.py                PluginCore: wires registry + engine + package store for main.py (docs/plugin-ui.md)
    screens/ sources/ packages/   screen engine, data sources, community packages (docs/*.md)
vendor-requirements.txt         the pinned, sha256-checked Pillow wheel; scripts/vendor.sh unpacks it into py_modules/ (gitignored)
tools/send_test.py edge_test.py hardware bench tools (same serialport, no pip packages)
tools/run_engine.py             run the engine on a real panel, or --emulator DIR for PNGs
tests/                          everything below
```

## Run everything

From the repo root (needs system CPython 3.14 x86_64 with `pytest` and `pip`, and `g++`). Pillow is not in git:
fetch the pinned wheel once (and again whenever `vendor-requirements.txt` changes; it is a no-op otherwise):

```bash
decky/scripts/vendor.sh        # sha256-checked cp314 Pillow -> decky/py_modules/PIL (gitignored)
python3 -m pytest decky/tests -q
```

That one command runs: vendored-Pillow and Decky-bundle stdlib checks (`test_vendor.py`),
no-pyserial guard (`test_no_pyserial.py`), `SerialPort` against a pty pair
(`test_serialport.py`), port discovery and `Link` (`test_ports.py`, `test_usb_link.py`),
protocol / diff / emulator (`test_proto.py`, `test_diff.py`, `test_emulator.py`), the
tools against the emulator (`test_send_test.py`), the plugin wrapper (`test_main.py`,
`test_frame.py`) and the firmware parser compiled with g++ and compared byte for byte with
the emulator (`test_cpp_diff.py`, builds `firmware/host_test/usbproto_host.cpp`).
The sleep hint (stdlib D-Bus client, logind delay lock, `Engine.prepare_sleep()/wake()`, the firmware bus-suspend detector built with
g++) is `test_sleep_idle.py` and `plugin/test_sleep_hint.py`; it runs against a fake bus socket (`tests/fakebus.py`) and never touches the
real system bus except in one test that is skipped where there is none. `tools/sleep_test.py` measures it on a real panel.

Plugin backend tests (`tests/plugin`, fake `decky` module + emulated panel) are part of that run. Frontend:

```bash
cd decky
npx pnpm@9 install --frozen-lockfile
npx pnpm@9 test          # vitest run
npx pnpm@9 coverage      # + v8 coverage table
npx pnpm@9 typecheck     # tsc --noEmit (src and test)
npx pnpm@9 build         # -> dist/index.js
scripts/build_idle_render.sh  # static idle-preview renderer -> bin/idle_render_host (gitignored; needs g++)
python3 scripts/make_zip.py   # -> out/inkterface-decky.zip (refuses to build unless scripts/vendor.sh and build_idle_render.sh ran)
```

Opt-in extras: `INKTERFACE_FROZEN=1` adds the PyInstaller-frozen stub (needs network and a
shared-lib python3.14); `pip install pyserial==3.5` enables the tty-state oracle test (test
only, never shipped).

Other checks:

```bash
cp -n firmware/src/ee05/idle_config.h.example firmware/src/ee05/idle_config.h   # gitignored local Wi-Fi/Steam config
(cd firmware && pio run -e ee05)                 # firmware builds
(cd decky && npx pnpm@9 install && npx pnpm@9 build)   # frontend -> dist/index.js
```

Hardware bench (needs a panel attached): `python3 decky/tools/send_test.py --list`.

## Hot reload while developing

`plugin.json` ships with `"flags": []`. In Decky Loader 3.3.1 the `"debug"` flag has exactly one effect: with Decky's
live reload on, a changed `dist/index.js` or `main.py` re-imports a plugin that is already loaded only if it has the flag
(`backend/decky_loader/loader.py`, `import_plugin`); without it the loader logs "requested to not be re-loaded" and keeps
the old copy. To get hot reload, add it to the **installed** copy on the Steam Machine, never in the repo:
edit `~/homebrew/plugins/Inkterface/plugin.json` to `"flags": ["debug"]` and restart Decky (`sudo systemctl restart plugin_loader`).
A re-install from a release zip puts the flag-free file back.
