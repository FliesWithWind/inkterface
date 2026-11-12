## What and why

<!-- One or two sentences. Link the issue. -->

## Checks (CONTRIBUTING.md has the commands)

- [ ] New logic has tests: the normal path and its edge cases (empty, malformed, boundary, failure). A bug fix has a test that fails without it.
- [ ] `decky/scripts/vendor.sh` once, then `python3 -m pytest decky/tests -q` passes
- [ ] `cd decky && npx pnpm@9 typecheck && npx pnpm@9 test && npx pnpm@9 build` pass (if the frontend changed)
- [ ] `cd firmware && pio run -e ee05` builds (if the firmware changed)
- [ ] Docs that name a file or link to a doc still resolve (`decky/tests/test_repo_paths.py`)
- [ ] No new file over 2 MB, no vendored binaries, no secrets, no `.env`, no panel flash backups
- [ ] `CHANGELOG.md` updated under Unreleased, if a user would notice

## What still needs a real panel or a Steam Machine

<!-- No hardware? Say what the emulator or the fakes cover and what they do not. -->

## Screens (delete if not a screen change)

- [ ] Golden image added or refreshed and reviewed (`pytest decky/tests/screens --update-goldens`)
- [ ] Code screens are not accepted as built-ins
