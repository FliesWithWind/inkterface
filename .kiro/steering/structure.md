---
inclusion: always
---

# Project structure

```
decky/                          Decky plugin
  main.py                       thin async Decky entry point
  src/                          React frontend (pages/, components/)
  test/                         frontend tests (vitest)
  py_modules/inkterface_core/   stdlib-only Python core
    proto.py diff.py emulator.py  frame codec, dirty rectangles, firmware-equivalent emulator
    usb_link.py serialport.py     panel discovery (VID 303a) and raw serial
    engine/                       settings, scheduler, render -> diff -> send loop
    screens/ sources/             screen schema and renderer, host data sources
    packages/ catalog/            zip import and approval, signed Screen Store client
    idle/                         compile idle layouts and upload them to the panel
    ota/                          signature check, manifest, GitHub release lookup, USB streaming
    plugin_core.py                wires everything for main.py
  screens/                      built-in screens (screen.json, sample-data.json)
  tests/                        Python tests (pytest), goldens, fakes
  tools/                        bench tools; hw_e2e.py, send_test.py etc. open the real panel
  scripts/                      vendor.sh, make_zip.py, build_idle_render.sh
firmware/                       PlatformIO project
  src/ee05/                     product firmware (USB protocol, UC8179 driver, idle mode, OTA)
  src/pbench/                   partial-refresh bench
  host_test/                    C++ tests compiled with g++ by pytest and CI
docs/                           documentation, indexed in docs/README.md
tools/                          check_file_sizes.py, gen_bom.py, release/ (key and signing tools)
hardware/                       bill of materials source (bom.yaml)
.github/workflows/              ci.yml, firmware-release.yml, plugin-release.yml
.kiro/                          Kiro specs, steering and hooks (this folder)
```

## Conventions

- Python files are `snake_case.py`; one test file per area under `decky/tests/<area>/test_*.py`.
- Screen folder name equals the screen `id` (lowercase `[a-z0-9-]`). A built-in screen needs a golden image test in `decky/tests/screens/`.
- Docs that name a repo path or link to another doc are checked by `decky/tests/test_repo_paths.py`. Every file under `docs/` is linked from `docs/README.md`.
- The version lives only in `VERSION`; `decky/package.json` must match (`decky/tests/test_version.py`).
- No file over 2 MB is committed (`tools/check_file_sizes.py`).
- The `.kiro/` folder is committed and must not be gitignored.
