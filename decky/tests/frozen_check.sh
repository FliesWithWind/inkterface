#!/usr/bin/env bash
# Closest-to-Decky check without SteamOS: freeze a stub with PyInstaller on a shared-lib CPython 3.14
# (stdlib extensions NOT analysed => missing like in Decky's bundle), append py_modules to sys.path, import the plugin.
# Needs a system python3.14 (not uv's static build, which has termios built in) and network for pip.
set -euo pipefail
here=$(cd "$(dirname "$0")/.." && pwd); w=$(mktemp -d); trap 'rm -rf "$w"' EXIT; cd "$w"
cat > app.py <<'PY'
import sys, os, asyncio, json, logging, ctypes, struct, zlib, threading, shutil, tempfile, socket, ssl, hashlib, sqlite3  # noqa  (Decky-ish imports; never termios)
sys.path.append(sys.argv[1])  # same as Decky's sandboxed_plugin.py
import PIL, inkterface_core
m, sl = os.openpty()
with inkterface_core.SerialPort.open(os.ttyname(sl)) as p:  # needs fcntl/select only; the bundle has no termios
    os.write(m, b"\\x00\\r\\n\\x11\\x13")
    assert p.read(16, timeout=1) == b"\\x00\\r\\n\\x11\\x13"
assert "termios" not in sys.modules
print("FROZEN", sys.version.split()[0], PIL.__version__, len(inkterface_core.render_test_frame()), "serial-ok")
PY
"${PYTHON:-/usr/bin/python3.14}" -m venv v && ./v/bin/pip install -q pyinstaller
./v/bin/pyinstaller --onefile -n frozapp app.py --log-level ERROR >/dev/null
./dist/frozapp "$here/py_modules"
