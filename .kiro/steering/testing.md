---
inclusion: fileMatch
fileMatchPattern: ["decky/tests/**", "decky/test/**", "firmware/host_test/**", "CONTRIBUTING.md"]
---

# Testing

Every change ships with tests, and a test run is shown before work is called done. CI must be green (`pytest`,
`firmware`, `plugin`) before merge. Details: #[[file:CONTRIBUTING.md]]

## Rules

- New logic gets tests for the normal path and its edge cases: empty, malformed, boundary, failure.
- A bug fix gets a test that fails without the fix.
- No hardware does not mean no test. Use the emulator (`decky/py_modules/inkterface_core/emulator.py`), fakes, or pty
  pairs, and say in the pull request what still needs a real panel.
- One test per behaviour, not per input. Put the full input list where the rule lives (validator, parser, store) and
  give the layer above one or two cases for the error shape. Never remove a regression or security test.
- Firmware logic that can run on a host goes in files compiled by `firmware/host_test/` (g++ with address and
  undefined-behaviour sanitizers); pytest builds and runs them and compares them byte for byte with the Python emulator.
- Built-in screens have golden images. Refresh with `python3 -m pytest decky/tests/screens --update-goldens` and review the PNG diff.
- Docs that name a file or link to another doc are checked by `decky/tests/test_repo_paths.py`. Moving a path means fixing the docs.

## Commands (repo root unless noted)

| Check | Command |
|-------|---------|
| Python | `decky/scripts/vendor.sh` once, then `python3 -m pytest decky/tests -q` |
| Types | `cd decky && npx pnpm@9 typecheck` |
| Frontend | `cd decky && npx pnpm@9 test` |
| Frontend build | `cd decky && npx pnpm@9 build` |
| OTA coverage gate | `coverage run --branch --include='decky/py_modules/inkterface_core/ota/*,decky/py_modules/inkterface_core/firmware.py' -m pytest -q decky/tests && coverage report --fail-under=90 -m` |
| Hygiene | `python3 tools/check_file_sizes.py` (no committed file over 2 MB) |

## Before every push

Run a hygiene scan of the tracked files: no absolute home paths, no LAN addresses, no device MACs or USB serials, no
personal names or account ids, and no Wi-Fi names in screenshots. Show the scan output with the test output.
