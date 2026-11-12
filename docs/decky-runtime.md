# Decky runtime constraints: serial port and Pillow

The plugin runs inside Decky Loader's Python process, which is a PyInstaller bundle. Only the modules
that bundle contains can be imported, plus whatever the plugin vendors in `py_modules/`. This page
records what the bundle contains, what that means for serial access and image rendering, and how to
re-check it for a new Decky release.

## Summary

- **Pillow in-process works.** One cp314 wheel imports and renders under the bundle's import model.
- **pyserial does not work.** Decky's bundle has no `termios`, so `import serial` fails. The plugin uses
  `inkterface_core/serialport.py` instead (stdlib only: `os`, `fcntl`, `select`, `struct`). pyserial is not
  vendored.

## Bundle inventory (Decky Loader v3.3.1)

Source: the x86_64 `PluginLoader` release binary (35 MB), listed with PyInstaller's `CArchiveReader`
(TOC, `PYZ.pyz`, `base_library.zip`, `lib-dynload`). Python is 3.14.x (`libpython3.14.so.1.0`; Decky's
`build.yml` pins 3.14.8).

| module | in bundle | where |
|---|---|---|
| `termios` | **no** | absent from PYZ, base lib and lib-dynload; also no `tty`, `pty` |
| `fcntl` | yes | lib-dynload |
| `select` | yes | lib-dynload |
| `ctypes` | yes | PYZ (`ctypes.util` too) |
| `_ctypes` | yes | lib-dynload |
| `zlib`, `_struct`, `binascii`, `math`, `array` | yes | lib-dynload |
| `errno` | builtin (shared-lib 3.14 build) | |

`termios` is missing because the PyInstaller spec lists `hiddenimports` (`logging.handlers`, `sqlite3`,
`http.server`, `socketserver`, `configparser`, ...) and Decky itself never imports `termios`. The full
list of stdlib module names in the bundle is `decky/tests/decky_3.3.1_stdlib.txt`.

The failure was reproduced in a PyInstaller-frozen stub (`decky/tests/frozen_check.sh`, system CPython
3.14.9, `py_modules` appended to `sys.path` the way Decky's `sandboxed_plugin.py` does it). With
pyserial the stub raises `ModuleNotFoundError: No module named 'termios'` from `serial/serialposix.py:38`.

Another Decky plugin that ships Pillow (JSAUX-E-INK) works around similar gaps by appending the
`sysconfig` stdlib path to reach `xml` and by preloading `pillow.libs/*.so` through `ctypes` with
`RTLD_GLOBAL`. Inkterface needs neither.

## Pillow and the bundle

`readelf -d` on the cp314 `.so` files:

- `PIL/_imaging*.so` has `RPATH $ORIGIN/../pillow.libs` and needs hash-named libraries shipped in the
  wheel (`libtiff-*`, `libjpeg-*`, `libopenjp2-*`, `libxcb-*`, `libfreetype-*`, `libharfbuzz-*`, ...).
  Their names differ from the bundle's own libraries (`libzstd.so.1`, `liblzma.so.5`, ...), so they
  cannot collide.
- Libraries not vendored: `libc`, `libm`, `libpthread`, `libdl`, `libgcc_s`, `libstdc++`, `libz.so.1`.
  Decky's bundle ships `libz.so.1`, `libgcc_s` and `libstdc++` (PyInstaller puts its temp dir on
  `LD_LIBRARY_PATH`), and the OS has them too. The highest glibc symbol version in any Pillow `.so` is
  `GLIBC_2.27`.
- The stdlib modules Pillow and `inkterface_core` import at runtime (`zlib`, `struct`, `threading`,
  `logging`, ...) are all in the bundle. `decky/tests/test_vendor.py` diffs them against
  `decky/tests/decky_3.3.1_stdlib.txt` on every run.
- The frozen stub rendered a 648x480 frame with FreeType text (`ImageFont.load_default(size=48)`), so
  `_imaging`, `_imagingft`, zlib and the vendored libraries all load.

## Serial port: decision

`inkterface_core/serialport.py` is a pure-Python shim on bundled modules. `fcntl.ioctl` with
`TCGETS`/`TCSETS` (kernel `struct termios`, 36 bytes) puts the tty in raw 8N1: no echo, no CR/LF
mapping, no XON/XOFF, no ISTRIP, VMIN=0. `TIOCMBIC`/`TIOCMBIS` set DTR and RTS. It is about 100 lines
and ships no binary.

Why not the alternatives:

- **Vendored compiled `termios` extension.** It works, but needs a per-ABI C build (an Arch build
  required `GLIBC_2.42`; a zig-built one required 2.2.5) and adds a binary to trust.
- **Subprocess with the SteamOS system python.** SteamOS ships `python3`, but its presence and version
  are not guaranteed for plugins, so the plugin does not depend on it.
- **Static helper process (Go).** Kept only as a fallback if the in-process approach fails on a real
  runtime. Other e-ink projects use a daemon for the same reason.

### SerialPort API

```python
from inkterface_core import SerialPort
```

- `SerialPort.open(path, dtr=False, rts=False)`
- `read(size, timeout) -> bytes`: returns `b""` only on timeout; raises `OSError(EIO)` when the device
  vanished.
- `write(data, timeout)`: writes all bytes or raises `TimeoutError`.
- `close()`, and context-manager support.

`list_serial_ports()` is a `glob` of `/dev/ttyACM*` and `/dev/ttyUSB*` (natural sort). There is no
VID/PID filtering yet.

### Known limits

- Linux only (`ENOSYS` elsewhere).
- Baud rate and parity are left untouched. That is right for USB CDC and ESP32-S3 USB-Serial-JTAG, and
  wrong for real UARTs.
- One reader at a time.
- The kernel raises DTR/RTS inside `open()`, so a brief high pulse before they are lowered cannot be
  avoided from userspace (pyserial has the same limit). It may still reset an ESP32 on connect.

## Vendored files

| File | Tag |
|---|---|
| `pillow-13.0.0-cp314-cp314-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl` | cp314 |

Sizes: `py_modules/` is 20.4 MB unzipped. The plugin zip (`dist`, `main.py`, `plugin.json`,
`package.json`, `py_modules`) is 7.1 MB (deflate -9). `pillow.libs` is 14.7 MB of the unzipped size,
mostly codecs the plugin does not use; they cannot be removed because `_imaging*.so` links them.

### One wheel, cp314 only

Decky v3.3.1 pins Python 3.14.8, and its `pyproject` allows `>=3.10,<3.15`, so older loaders ran older
Pythons (JSAUX-E-INK vendors cp311, cp313 and cp314 side by side). Several ABIs can coexist in one `PIL/`
directory because only the `_imaging*.so` files carry the ABI tag. Supporting another ABI means adding
its wheel and sha256 to `decky/vendor-requirements.txt` and its `--abi` to `decky/scripts/vendor.sh`
(cp313 adds 1.7 MB zipped). A wrong ABI fails loudly with
`ImportError: cannot import name '_imaging' from 'PIL'`; a test covers this.

## Reproduce

From the repo root; `uv` and network access are needed on the first run. The full run without `uv` is in
[`decky/README.md`](../decky/README.md).

```
uv run --python 3.14 --no-project --with pytest pytest decky/tests -q                        # all tests except the frozen stub
uv run --python 3.14 --no-project --with pytest --with pyserial==3.5 pytest decky/tests -q   # + pyserial oracle comparison
INKTERFACE_FROZEN=1 uv run --python 3.14 --no-project --with pytest pytest decky/tests -q    # + PyInstaller-frozen stub (needs system python3.14)
(cd decky && npx --yes pnpm@9 exec tsc --noEmit --skipLibCheck)                             # frontend typecheck
(cd decky && npx --yes pnpm@9 install && npx --yes pnpm@9 build)                             # frontend -> dist/index.js
decky/scripts/vendor.sh                                                                      # fetch the pinned Pillow (vendor-requirements.txt)
```

### Regenerating the bundle module list for a newer Decky

Open the release `PluginLoader` binary with `PyInstaller.archive.readers.CArchiveReader` and collect
the module names from `PYZ.pyz`, `base_library.zip` and the `lib-dynload` entries. Write them to
`decky/tests/decky_<version>_stdlib.txt` and update the `BUNDLED` path in `decky/tests/test_vendor.py`.

## What the tests cover

- `decky/tests/test_serialport.py` uses a pty pair, so the kernel tty driver is real.
  - Round trip in both directions for NUL, LF, CR, CRLF, XON 0x11, XOFF 0x13, ^C, ^Z, 0xFF and all 256
    byte values; the same on a deliberately "dirty" tty (every flag set, 7E1).
  - A negative control shows that a plain `os.open` of a pty alters bytes.
  - Flags are checked against stdlib `termios` as an oracle; ioctl constants and struct size are checked
    against `termios`.
  - Backpressure (300 KB write with a draining peer), write timeout, read timeout, partial reads, peer
    hangup giving `EIO`, close idempotence, use after close, missing path, non-tty targets (`/dev/null`,
    a regular file) with an fd-leak check.
  - DTR/RTS ioctl calls: default low, high, mixed, non-modem tty tolerated, real errors propagated and
    the fd closed.
  - Refusal on non-Linux.
  - An opt-in comparison with pyserial 3.5's resulting tty state (iflag, oflag, lflag, a cflag subset,
    VMIN and VTIME identical).
- `decky/tests/test_frame.py`, `decky/tests/test_ports.py` (fake `glob`) and `decky/tests/test_main.py`
  (fake `decky` module, `plugin.json` sanity, core has no `decky` imports).
- `decky/tests/test_vendor.py`: isolated import with only `py_modules` on `sys.path`; a stdlib-versus-Decky-bundle
  audit (including a pty round trip, and a test that the audit fails when the allowlist lacks a used
  module); wrong-ABI Pillow raises.
- `decky/tests/frozen_check.sh` / `INKTERFACE_FROZEN=1`: the above inside a PyInstaller-frozen process
  that has no `termios`.
- Mutation spot checks on `serialport.py` (dropping IXON, ISTRIP, OPOST, ECHO clearing, the EOF check,
  fd cleanup, DTR handling) each made at least one test fail. The first ISTRIP survivor led to the
  "dirty tty" tests.

## Not verified

- **Decky Loader on SteamOS.** The frozen stub approximates it (PyInstaller, `py_modules` appended to
  `sys.path`, 3.14.9 versus 3.14.8). Nothing was tested inside Decky's own `PluginLoader` process.
- **The EE05 panel over this serial path.** The pty tests do not model USB: no DTR/RTS lines (modem
  ioctls answer `ENOTTY`, so line levels are only asserted through recorded ioctl calls), no unplug
  semantics other than hangup, no CDC throughput, no ESP32 reset on open.
- **Serial permissions.** Without `"flags": ["root"]` the plugin runs as `deck` (`os.setuid`) with no
  `setgroups`/`initgroups` (`localplatformlinux.py`), so supplementary groups such as `uucp` are not
  applied. `/dev/ttyACM*` is usually `root:uucp 0660`, so `open` fails with `EACCES` unless a udev
  `uaccess` tag or ACL covers the device. Only the exact string `"root"` in `flags` keeps root
  (`sandboxed_plugin.py`); the plugin template's `"_root"` is an inert placeholder. Other Decky plugins
  checked for precedent (monoink, JSAUX-E-INK) use BLE with `"flags": []`, none uses serial. `plugin.json`
  ships without `root`; the remedy is either adding `root` or shipping a udev rule.
- glibc on current SteamOS against the Pillow wheel (`manylinux_2_28`, highest symbol `GLIBC_2.27`):
  expected to be fine.
- Plugin-store policy for `root` plugins.
- Older loaders (cp311/cp313) at runtime.
