#!/usr/bin/env python3
"""Fail if a tracked file is bigger than 2 MB and not in tools/large-files-allowlist.txt.

Run from anywhere in the repo: python3 tools/check_file_sizes.py [--limit BYTES]
The allowlist is empty today. Do not add to it: put big
media somewhere else (a release asset, a link) and see CONTRIBUTING.md.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

LIMIT = 2 * 1024 * 1024
HERE = Path(__file__).resolve().parent


def allowlist(path: Path) -> set[str]:
    return {ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.lstrip().startswith("#")}


def tracked(root: Path) -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True).stdout
    return [p.decode() for p in out.split(b"\0") if p]


def too_big(root: Path, allowed: set[str], limit: int = LIMIT) -> list[tuple[str, int]]:
    """(path, size) of every tracked file over `limit` bytes that is not allowlisted. Deleted-but-tracked files are skipped."""
    found = []
    for rel in tracked(root):
        f = root / rel
        if rel not in allowed and f.is_file() and f.stat().st_size > limit:
            found.append((rel, f.stat().st_size))
    return found


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--limit", type=int, default=LIMIT)
    args = ap.parse_args(argv)
    root = HERE.parent
    bad = too_big(root, allowlist(HERE / "large-files-allowlist.txt"), args.limit)
    for rel, size in bad:
        print(f"{rel}: {size / 1e6:.1f} MB is over the {args.limit / 1e6:.1f} MB limit (CONTRIBUTING.md: no new large binaries)", file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
