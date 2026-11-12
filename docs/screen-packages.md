# Screen packages: share, install, approve

A screen package is a folder with a `screen.json` (see [screen-format.md](screen-format.md)) zipped for sharing.
There are two tiers:

| Tier | Contains | What happens on install |
|------|----------|-------------------------|
| Declarative | `screen.json` + `assets/` | Validated, installed, **enabled**. No code runs. |
| Code | the same + one `screen.py` | Validated, installed **disabled**, waits for the user's approval. |

Working examples: `decky/examples/hello-screen/` (declarative) and `decky/examples/hello-code-screen/` (code).
All commands below run from `decky/py_modules` (or set `PYTHONPATH` to it); Python 3.11+ and the vendored Pillow are all you need.

## For authors

### 1. Write it

```
my-screen/            folder name == "id" in screen.json
  screen.json
  assets/             optional
  screen.py           optional, code tier only
  sample-data.json    optional, used by the emulator
```

For the code tier set `"active": {"code": "screen.py", "widgets": []}` and write:

```python
def render(canvas_size, data, settings):
    # canvas_size == (648, 480); data = the host's data snapshot as a plain dict;
    # settings = your screen.json settings with the user's values applied
    ...
    return png_bytes          # or a PIL image
```

The result must be a **648x480, 1-bit** (`Image.new("1", ...)`) PNG. Anything else becomes an error screen.
`print()` goes to the plugin log, not the panel. Rules for code packages:

- one file only: the code must be `screen.py`; any other `.py`, `.pyc`, `.pyd`, `.so`, `.dll`, `.dylib` or `.sh` is rejected
  (the user approves one hash, so there can be only one thing to hash)
- standard library and Pillow only; finish in well under 2 s; stay under about 512 MB of memory
- you cannot start processes or threads, and files you write are capped at 2 MB

### 2. Test it with the emulator (no panel needed)

```bash
python -m inkterface_core.screens.emulate my-screen --out out.png                # declarative
python -m inkterface_core.screens.emulate my-screen --allow-code --out out.png   # runs screen.py (guarded)
python -m inkterface_core.screens.emulate my-screen --set greeting=Moin          # override a setting
```

Without `--allow-code` a code screen refuses to render, so a folder you downloaded never runs by accident.
Errors name the file and the field, for example `screen.json: active.widgets[3].x: widget extends past the 648px edge`.
A failing `screen.py` exits 1 with its exception text.

### 3. Package it

```bash
python decky/examples/make_package.py my-screen            # -> my-screen-1.0.0.zip next to the folder
python decky/examples/make_package.py my-screen -o out.zip
```

It validates the folder, writes the zip with the layout the importer needs (a single `my-screen/` root, plain files,
fixed timestamps so builds are reproducible, no hidden files, `__pycache__`, `.pyc` or other zips), then re-imports the zip
as a last check. Zip it by hand and you are on your own: the importer is strict (see the limits below).

Check exactly what a user will get, without installing anything:

```bash
python -m inkterface_core.screens.emulate --package my-screen-1.0.0.zip --allow-code --out out.png
```

### 4. Share it

**For everyone: submit it to the catalog.** Open a pull request on the catalog repo (`github.com/FliesWithWind/inkterface-screens`, put your folder in
`screens/<id>/`). CI validates it with this repo's own validator, a reviewer approves it (code screens get extra review), and after the merge it
appears in the plugin's **Screen Store** page, signed. See [screen-catalog.md](screen-catalog.md) and that repo's README.

**For development: a zip.** Put the zip somewhere reachable over **https** (a GitHub release asset works) or copy it to the device, and import it
with Developer mode on (below). Bump `version` for every release: an upgrade must be strictly newer (semver) than what is installed. Tell code-tier
users the `sha256` of your `screen.py` (`sha256sum my-screen/screen.py`): the installer shows the same value, so they can compare.

## For users

**Install from the Screen Store** (plugin, Screen Store page): the catalog is signed, and every package goes through the importer checks below. A normal user never
needs anything else.

**Developer import.** Installing from a local zip or an https URL (`import_package` / `PackageStore.install(source)`) is a developer feature and is
refused until **Settings, Developer mode** is on (off by default). Nothing reviews what you import this way. The checks below and the approval step
are identical for both routes.

- a declarative package is enabled right away; disable or uninstall it any time
- a code package shows **id, version, author and the sha256 of `screen.py`** and stays off until you approve exactly that
  version and hash; the author is whatever the package says, so approve only code you trust
- editing `screen.py` afterwards (or replacing it) revokes the approval and disables the screen; the changed code never runs
  until you approve the new hash
- upgrading a code package needs a new approval, even if the code is byte-identical
- if an upgrade is invalid, the installed version stays as it was; on success the old version is deleted
- ids of built-in screens (`telemetry`, `big-clock`, ...) cannot be used; built-ins can be disabled, not uninstalled

Store layout (`DECKY_PLUGIN_SETTINGS_DIR/screens/` in Decky): `<id>/<version>/` per package plus `state.json`
(enabled flags and approvals). If `state.json` is unreadable it is kept as `state.json.bad` and the store starts empty:
approvals are forgotten, never guessed.

## Security model and its limits

### What the importer enforces (all before anything is installed)

| Check | Limit |
|-------|-------|
| Download | https only, redirects to http refused, 5 MiB, 15 s for the whole transfer |
| Zip file size | 5 MiB |
| Entries | at most 200; one top-level folder `<id>/` |
| Names | no absolute paths, `..`, `.` or empty segments, backslashes, control characters, drive letters; valid UTF-8; no duplicates (case-insensitive) |
| Entry types | regular files and directories only: symlinks, device files, FIFOs and sockets are refused |
| Size | 4 MiB per file, 20 MiB total, compression ratio above 100:1 on entries over 1 MiB refused; deflate or store only; no encryption |
| Extraction | into a scratch folder with exclusive file creation, then validated with the normal `screen.json` validator, then an atomic rename |
| Code | only `screen.py`, and only if `screen.json` declares it |

### What the `screen.py` guardrail does

The approved bytes are hashed and re-checked on every run, then executed in a **separate process** (`python -I`): an empty
environment (no inherited secrets), working directory = the package folder, wall-clock timeout of 2 s (the whole process group is
killed), `setrlimit` caps on memory (512 MB), CPU time, file size (2 MB), open files and process creation, and a 1 MiB cap on what it
may send back. The returned image is checked: it must be a PNG, exactly 648x480, 1-bit. A screen that crashes, hangs, exits,
prints garbage or returns the wrong thing makes the host draw an **error screen** instead; the plugin process is never affected.

### What it does NOT do

**This is a guardrail against buggy screens, not a sandbox.** An approved `screen.py` is a program running as the same user
as the plugin. It can read and write that user's files, use the network and look at anything the user can. Specifically:

- no filesystem isolation (no chroot, namespaces, seccomp or Landlock); cwd and the empty environment are conveniences
- no network isolation
- `RLIMIT_NPROC` is not enforced for root, and the memory limit is not enforced on macOS; Linux as a normal user is the supported setup
- the approval is a hash of one file: it proves "this is the code you saw", not "this code is safe"
- the interpreter is whatever `python3` the host provides (inside the frozen Decky loader `sys.executable` is not a Python, so
  `python3` from `PATH` is used); the vendored Pillow only loads if that Python matches its ABI (cp314), otherwise a screen that
  imports `PIL` fails with an import error shown on the error screen

The only real protection is the approval step: install code packages only from authors you trust, and read the code.
