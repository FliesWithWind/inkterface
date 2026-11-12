#!/usr/bin/env bash
# Fetch the pinned Pillow and certifi wheels (decky/vendor-requirements.txt, sha256-checked) into py_modules/. Needs python3 with pip.
# Run before the Python tests and before make_zip.py; the vendored tree is gitignored. Idempotent: does nothing when
# py_modules/.vendored already records the current requirements file.
# (Serial I/O is pure stdlib: inkterface_core/serialport.py. Decky v3.3.1 ships CPython 3.14.x, hence cp314 only.)
set -euo pipefail
cd "$(dirname "$0")/.."
dest=${VENDOR_DEST:-py_modules}
req=${VENDOR_REQUIREMENTS:-vendor-requirements.txt}
want=$(sha256sum "$req" | cut -d' ' -f1)
if [ -f "$dest/.vendored" ] && [ -f "$dest/PIL/Image.py" ] && [ -f "$dest/certifi/cacert.pem" ] && [ "$(head -n1 "$dest/.vendored")" = "$want" ]; then
  echo "vendor: $dest is up to date"; exit 0
fi
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
rm -rf "$dest/.vendored" "$dest/PIL" "$dest"/pillow* "$dest/certifi" "$dest"/certifi-*
# --require-hashes: pip aborts, writing nothing usable, if the wheel's sha256 is not the pinned one.
python3 -m pip download -r "$req" --require-hashes --no-deps --only-binary=:all: \
  --python-version 314 --implementation cp --abi cp314 --abi abi3 --abi none \
  --platform manylinux_2_28_x86_64 --platform manylinux_2_17_x86_64 --platform manylinux2014_x86_64 -d "$tmp" -q
wheels=("$tmp"/*.whl)
[ "${#wheels[@]}" -eq 2 ] || { echo "vendor: expected exactly two wheels (pillow, certifi), got ${#wheels[@]}" >&2; exit 1; }
mkdir -p "$dest"
stamp=$(printf '%s\n' "$want")
for w in "${wheels[@]}"; do
  python3 -m zipfile -e "$w" "$dest/"
  stamp+=$'\n'"$(sha256sum "$w" | cut -d' ' -f1)  $(basename "$w")"
done
rm -f "$dest"/*.dist-info/RECORD
find "$dest" -name __pycache__ -prune -exec rm -rf {} +
# Stamp last, so an interrupted run is never mistaken for a finished one: line 1 = hash of the requirements file,
# lines 2.. = sha256 of each wheel that was unpacked (tests check each is one of the pinned hashes).
printf '%s\n' "$stamp" > "$dest/.vendored"
echo "vendor: $(basename -a "${wheels[@]}" | tr '\n' ' ')-> $dest"
