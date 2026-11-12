"""The screen-catalog signing public key the plugin trusts.

A SEPARATE key from the firmware release key (ota/trust.py): the catalog is a different
publisher. The 64-hex-char PUBLIC key is pasted here by the maintainers. If it is empty the
plugin refuses every catalog read and install (fail closed):
refresh, list and install all say "no catalog signing key is bundled". Never put a private
key in this repo.

The TEST-ONLY key reaches the client only through a constructor argument / the main.catalog_kw
code seam (never env, config or a callable); see decky/tests/test_catalog_testkey_not_shipped.py.
"""
from __future__ import annotations

PRODUCTION_PUBLIC_KEY_HEX = "e0b97f272189eee44e173b700a03c995caa10c0cce64b56a43f1b94bdc01eba2"  # public half of the catalog signing key (private half: GitHub secret INKTERFACE_CATALOG_KEY)


def public_key() -> bytes | None:
    return bytes.fromhex(PRODUCTION_PUBLIC_KEY_HEX) if PRODUCTION_PUBLIC_KEY_HEX else None
