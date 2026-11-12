#!/usr/bin/env python3
"""Check a release directory exactly like the plugin would: signature with the bundled public
key (trust.py, or --pubkey HEX), schema, and the .bin's size and sha256.

    python3 tools/release/verify_release.py dist/            # CI runs this before publishing
    python3 tools/release/verify_release.py dist/ --pubkey <64 hex>

Exit 0 = the plugin would accept this release. Catches a wrong or stale signing secret
before anything is published.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "decky", "py_modules"))

from inkterface_core.ota import errors, manifest as M, trust  # noqa: E402


def check(directory: str, public_key: bytes | None) -> M.Manifest:
    def read(name: str) -> bytes:
        with open(os.path.join(directory, name), "rb") as f:
            return f.read()

    m = M.verify_and_parse(read("manifest.json"), read("manifest.sig"), public_key)
    image = read(m.asset)
    if len(image) != m.size or hashlib.sha256(image).hexdigest() != m.sha256:
        raise errors.HashMismatch(f"{m.asset} does not match the signed manifest")
    return m


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dir")
    ap.add_argument("--pubkey", help="64 hex chars; default: the key bundled in inkterface_core/ota/trust.py")
    a = ap.parse_args(argv)
    key = bytes.fromhex(a.pubkey) if a.pubkey else trust.public_key()
    try:
        m = check(a.dir, key)
    except (errors.OtaError, OSError) as e:
        print(f"REJECTED: {e}", file=sys.stderr)
        return 1
    print(f"OK: {m.asset} v{m.version} protocol {m.protocol_version} {m.size} bytes sha256 {m.sha256[:12]}...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
