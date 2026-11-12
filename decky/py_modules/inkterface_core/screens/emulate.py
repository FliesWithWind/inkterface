"""Host-side PNG emulator.

    python -m inkterface_core.screens.emulate <package_dir> --data snapshot.json --out out.png
    python -m inkterface_core.screens.emulate --package my-screen-1.0.0.zip --out out.png   # validate + render, no install
    python -m inkterface_core.screens.emulate --all <dir> [--out out_dir]

A screen with a screen.py only renders with --allow-code (it runs in a guarded child process, not a sandbox).
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from ..packages.extract import load_zip
from ..packages.sandbox import run_code
from .data import DataSnapshot
from .renderer import render
from .schema import Screen, ScreenError, load_screen


def _read_json(path: Path):
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as e:
        raise ScreenError(f"{path}: cannot read data: {e}") from None


def _parse_settings(items: list[str]) -> dict:
    out = {}
    for item in items:
        k, sep, raw = item.partition("=")
        if not sep:
            raise ScreenError(f"--set {item!r}: expected key=value")
        try:
            out[k] = json.loads(raw)   # 5, true, "x"
        except ValueError:
            out[k] = raw               # bare string
    return out


def _render_screen(screen: Screen, out: Path, data_file: Path | None, settings: dict | None, allow_code: bool) -> None:
    data_file = data_file or screen.root / "sample-data.json"
    data = _read_json(data_file) if data_file.is_file() else {}
    if not isinstance(data, dict):
        raise ScreenError(f"{data_file}: top level must be an object")
    if screen.code_path is None:
        img = render(screen, DataSnapshot(data, data_file.parent), settings)
    elif allow_code:
        img = run_code(screen.code_path.read_bytes(), screen.root, data, screen.resolve_settings(settings))
    else:
        raise ScreenError("screen.py: this screen runs code; pass --allow-code to run it (guarded child process, not a sandbox)")
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "PNG")


def emulate(pkg: Path, out: Path, data_file: Path | None = None, settings: dict | None = None,
            allow_code: bool = False) -> Screen:
    screen = load_screen(pkg)
    _render_screen(screen, out, data_file, settings, allow_code)
    return screen


def emulate_zip(zip_path: Path, out: Path | None, data_file: Path | None = None, settings: dict | None = None,
                allow_code: bool = False) -> tuple[Screen, Path]:
    """Validate a package zip exactly like the installer does and render it, without installing anything."""
    with tempfile.TemporaryDirectory(prefix="inkterface-emulate-") as td:
        screen = load_zip(zip_path, Path(td))
        out = out or Path(f"{screen.id}.png")
        _render_screen(screen, out, data_file, settings, allow_code)
    return screen, out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m inkterface_core.screens.emulate", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("package", nargs="?", type=Path, help="screen package folder")
    ap.add_argument("--all", type=Path, metavar="DIR", help="render every package folder inside DIR")
    ap.add_argument("--package", dest="zip", type=Path, metavar="ZIP", help="validate + render a package zip without installing it")
    ap.add_argument("--allow-code", action="store_true", help="run the screen's screen.py (guarded child process, not a sandbox)")
    ap.add_argument("--data", type=Path, help="snapshot JSON (default: <package>/sample-data.json)")
    ap.add_argument("--out", type=Path, help="PNG path, or output folder with --all (default: ./<id>.png, ./out/)")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", help="override a screen setting")
    args = ap.parse_args(argv)
    if bool(args.package) + bool(args.all) + bool(args.zip) != 1:
        ap.error("give either a package folder or --all DIR (or --package ZIP)")
    try:
        settings = _parse_settings(args.set)
    except ScreenError as e:
        ap.error(str(e))

    if args.all:
        pkgs = sorted(p for p in args.all.iterdir() if (p / "screen.json").is_file()) if args.all.is_dir() else []
        if not pkgs:
            print(f"no screen packages in {args.all}", file=sys.stderr)
            return 1
        outdir = args.out or Path("out")
        failed = 0
        for p in pkgs:
            try:
                if load_screen(p).active is None:  # idle-only: rendered by the panel, nothing to emulate here
                    print(f"skip  {p.name} (idle-only)")
                    continue
                emulate(p, outdir / f"{p.name}.png", args.data, settings, args.allow_code)
                print(f"ok    {p.name} -> {outdir / (p.name + '.png')}")
            except ScreenError as e:
                failed += 1
                print(f"FAIL  {p.name}\n" + "\n".join(f"      {m}" for m in e.errors), file=sys.stderr)
        return 1 if failed else 0

    try:
        if args.zip:
            _, out = emulate_zip(args.zip, args.out, args.data, settings, args.allow_code)
            print(f"ok    {args.zip} -> {out}")
            return 0
        out = args.out or Path(f"{args.package.resolve().name}.png")
        emulate(args.package, out, args.data, settings, args.allow_code)
    except ScreenError as e:
        print("\n".join(e.errors), file=sys.stderr)
        return 1
    print(f"ok    {args.package} -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
