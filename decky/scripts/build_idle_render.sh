#!/usr/bin/env bash
# Build decky/bin/idle_render_host: the firmware's own idle renderer compiled for the host, statically linked,
# so the plugin can preview idle screens on SteamOS (which has no compiler). make_zip.py ships it; it is gitignored.
# Needs g++ with static libstdc++/libc. Idempotent: does nothing while the binary is newer than every input.
set -euo pipefail
cd "$(dirname "$0")/.."
fw=../firmware
out=bin/idle_render_host
srcs=("$fw/host_test/idle_render_host.cpp" "$fw/src/ee05/usbproto.cpp")
for s in idle_render idle_canvas idle_layout idle_fonts_data; do srcs+=("$fw/src/ee05/$s.cpp"); done
if [ -x "$out" ] && [ -z "$(find "$fw/src/ee05" "$fw/host_test/idle_render_host.cpp" scripts/build_idle_render.sh \
      \( -name '*.cpp' -o -name '*.h' -o -name '*.sh' \) -newer "$out" -print -quit)" ]; then
  echo "idle_render_host: $out is up to date"; exit 0
fi
mkdir -p bin
tmp=$(mktemp "$out.XXXXXX"); trap 'rm -f "$tmp"' EXIT
g++ -static -O2 -std=c++17 -Wall -Wextra -Werror -I"$fw/src/ee05" "${srcs[@]}" -o "$tmp"
chmod 755 "$tmp"
mv -f "$tmp" "$out"
echo "idle_render_host: built $out"
