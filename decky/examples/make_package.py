#!/usr/bin/env python3
"""Zip a screen folder into an installable package: python make_package.py my-screen [-o my-screen-1.0.0.zip]

Validates the folder first (same checks as the installer) and re-imports the zip as a final check.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py_modules"))

from inkterface_core.packages import make_zip  # noqa: E402
from inkterface_core.screens import ScreenError  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", type=Path)
    ap.add_argument("-o", "--out", type=Path, help="zip path (default: <id>-<version>.zip next to the folder)")
    args = ap.parse_args(argv)
    try:
        print(make_zip(args.folder, args.out))
    except ScreenError as e:
        print("\n".join(e.errors), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
