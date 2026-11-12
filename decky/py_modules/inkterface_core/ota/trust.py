"""The release signing public key the plugin trusts.

The maintainers generate the production key (tools/release/gen_keypair.py, see
docs/RELEASING.md step 1 and 3) and paste the 64-hex-char PUBLIC key here. If it is empty the
plugin refuses every release (fail closed) and firmware-release.yml cannot publish: it
verifies the signed release against this key first. Never put a private key in this repo.
"""
from __future__ import annotations

PRODUCTION_PUBLIC_KEY_HEX = "6472db23eb49864fa33cfe2d7d866927abf253c1013903f7af7d821c17b933cd"  # production release key (ed25519), docs/RELEASING.md


def public_key() -> bytes | None:
    return bytes.fromhex(PRODUCTION_PUBLIC_KEY_HEX) if PRODUCTION_PUBLIC_KEY_HEX else None
