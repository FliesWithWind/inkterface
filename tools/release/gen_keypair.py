#!/usr/bin/env python3
"""Generate an Ed25519 release-signing keypair (stdlib only).

    python3 tools/release/gen_keypair.py --private ~/inkterface-signing.key --public ~/inkterface-signing.pub

Writes the 32-byte seed as 64 hex chars (mode 0600, refuses to overwrite) and the public key
as 64 hex chars. Paste the PUBLIC hex into decky/py_modules/inkterface_core/ota/trust.py and
store the PRIVATE file's contents as the GitHub Actions secret INKTERFACE_SIGNING_KEY.
Never commit the private file. docs/ota.md has the full procedure.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "decky", "py_modules"))

from inkterface_core.ota import ed25519  # noqa: E402


def _write_new(path: str, text: str, mode: int) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)  # O_EXCL: never clobber a key
    with os.fdopen(fd, "w") as f:
        f.write(text)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--private", required=True, help="where to write the private seed (hex)")
    ap.add_argument("--public", required=True, help="where to write the public key (hex)")
    a = ap.parse_args(argv)
    seed = os.urandom(32)
    pub = ed25519.publickey(seed)
    try:
        _write_new(a.private, seed.hex() + "\n", 0o600)
        try:
            _write_new(a.public, pub.hex() + "\n", 0o644)
        except FileExistsError:
            os.unlink(a.private)  # do not leave an orphan private key behind
            raise
    except FileExistsError as e:
        print(f"refusing to overwrite {e.filename}", file=sys.stderr)
        return 1
    print(f"private key: {a.private}  (keep secret; 0600)")
    print(f"public key : {a.public}")
    print(f"PRODUCTION_PUBLIC_KEY_HEX = \"{pub.hex()}\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
