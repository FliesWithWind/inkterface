"""What ships: the production catalog public key is bundled and well formed, it is not the firmware key, and the default URL is the final Pages URL."""
import re

from inkterface_core.catalog import trust
from inkterface_core.catalog.client import DEFAULT_URL
from inkterface_core.ota import trust as ota_trust


def test_production_catalog_key_is_a_bundled_32_byte_public_key():
    assert re.fullmatch(r"[0-9a-f]{64}", trust.PRODUCTION_PUBLIC_KEY_HEX)
    assert trust.public_key() == bytes.fromhex(trust.PRODUCTION_PUBLIC_KEY_HEX) and len(trust.public_key()) == 32
    assert trust.PRODUCTION_PUBLIC_KEY_HEX != "0" * 64


def test_the_catalog_key_is_not_the_firmware_key():
    fw = getattr(ota_trust, "PRODUCTION_PUBLIC_KEY_HEX", "")
    assert trust.PRODUCTION_PUBLIC_KEY_HEX != fw or fw == ""


def test_default_catalog_url_is_the_final_github_pages_url():
    assert DEFAULT_URL == "https://flieswithwind.github.io/inkterface-screens/"
    assert DEFAULT_URL.startswith("https://") and DEFAULT_URL.endswith("/")


def test_no_private_key_material_in_the_tree():
    from pathlib import Path
    for p in Path(trust.__file__).parent.rglob("*.py"):
        assert not re.search(r"(?i)private[_ ]key\s*=\s*['\"][0-9a-f]{64}", p.read_text()), p
