#!/usr/bin/env python3
"""Build the Decky plugin zip: python3 decky/scripts/make_zip.py [out.zip]  (run `scripts/vendor.sh` and `npx pnpm@9 build` first).

Layout Decky expects: one top-level folder named after the plugin, holding dist/index.js, main.py, plugin.json,
package.json, LICENSE, bin/idle_render_host (static, run scripts/build_idle_render.sh first), screens/, py_modules/ and
dist/assets/ (images the bundle imports; Decky serves them from there). Source maps, __pycache__ and tests stay out.
"""
from __future__ import annotations

import hashlib
import re
import sys
import zipfile
from pathlib import Path

NAME = "Inkterface"
FILES = ["main.py", "plugin.json", "package.json", "LICENSE", "dist/index.js", "bin/idle_render_host"]
TREES = ["py_modules", "screens"]
SKIP = {"__pycache__"}
ASSET_URL = re.compile(rb"/plugins/" + NAME.encode() + rb"/assets/([\w.-]+)")   # what rollup-plugin-import-assets writes into the bundle
STAMP = "py_modules/.vendored"   # written last by scripts/vendor.sh; line 1 = sha256 of vendor-requirements.txt


def check_vendored(root: Path) -> None:
    """Refuse to zip a plugin that lacks the pinned Pillow or CA bundle (not in git): run scripts/vendor.sh first."""
    stamp, req = root / STAMP, root / "vendor-requirements.txt"
    want = hashlib.sha256(req.read_bytes()).hexdigest() if req.is_file() else None
    have = stamp.read_text().split("\n", 1)[0] if stamp.is_file() else None
    if want is None or have != want or not (root / "py_modules/PIL/Image.py").is_file() \
            or not (root / "py_modules/certifi/cacert.pem").is_file():
        raise FileNotFoundError(f"{root / 'py_modules/PIL'} is missing or does not match vendor-requirements.txt "
                                "(run decky/scripts/vendor.sh)")


def check_assets(root: Path) -> None:
    """Every image the bundle points at must be in dist/assets, or the plugin ships a broken icon."""
    for m in ASSET_URL.finditer((root / "dist/index.js").read_bytes()):
        if not (root / "dist/assets" / m.group(1).decode()).is_file():
            raise FileNotFoundError(f"{root / 'dist/assets' / m.group(1).decode()} is missing (dist/index.js imports it; run `npx pnpm@9 build`)")


def make_zip(root: Path, out: Path) -> int:
    """Write the zip, return the number of files in it. Raises FileNotFoundError naming the missing piece."""
    check_vendored(root)
    members = []
    for f in FILES:
        if not (root / f).is_file():
            hint = {"dist": " (run `npx pnpm@9 build`)", "bin": " (run decky/scripts/build_idle_render.sh)"}.get(f.split("/")[0], "")
            raise FileNotFoundError(f"{root / f} is missing{hint}")
        members.append(root / f)
    check_assets(root)
    for t in TREES:
        if not (root / t).is_dir():
            raise FileNotFoundError(f"{root / t} is missing")
        members += sorted(p for p in (root / t).rglob("*") if p.is_file() and not SKIP & set(p.relative_to(root).parts)
                          and p.suffix != ".pyc"
                          and p.name != ".vendored")
    members += sorted(p for p in (root / "dist/assets").glob("*") if p.is_file())
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in members:
            z.write(p, f"{NAME}/{p.relative_to(root).as_posix()}")   # keeps the file mode; Decky may still drop the exec bit on extract, so the plugin runs a private copy
    return len(members)


if __name__ == "__main__":
    here = Path(__file__).resolve().parents[1]
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else here / "out" / "inkterface-decky.zip"
    n = make_zip(here, target)
    print(f"{target}: {n} files, {target.stat().st_size / 1e6:.1f} MB")
