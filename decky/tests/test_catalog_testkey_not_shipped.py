"""The TEST-ONLY catalog signing key reaches the client only through the `main.catalog_kw` code seam (like the
OTA key through `main.firmware_kw`). A release build (the plugin zip as shipped) must trust the production
catalog key and nothing else: no environment variable, config file or UI callable can add a key."""
import inspect
import re
import sys
from pathlib import Path

import pytest

DECKY = Path(__file__).resolve().parents[1]
CORE = DECKY / "py_modules" / "inkterface_core"
sys.path.insert(0, str(DECKY / "scripts"))

import make_zip as MZ  # noqa: E402
from inkterface_core import plugin_core as PC  # noqa: E402
from inkterface_core.catalog import trust  # noqa: E402

# every shipped module that is part of the catalog trust path
SHIPPED_TRUST_CODE = [DECKY / "main.py", CORE / "plugin_core.py", *sorted((CORE / "catalog").glob("*.py"))]
ENV_NAMES = ["INKTERFACE_CATALOG_PUBKEY", "INKTERFACE_CATALOG_KEY", "INKTERFACE_TEST_PUBKEY",
             "CATALOG_PUBKEY", "CATALOG_KEY", "PUBLIC_KEY"]


def test_no_shipped_catalog_code_reads_the_environment():
    bad = [f.name for f in SHIPPED_TRUST_CODE if re.search(r"os\.environ|getenv|environ\[|sys\.argv", f.read_text())]
    assert len(SHIPPED_TRUST_CODE) >= 5 and bad == []


def test_production_catalog_key_is_the_one_bundled_constant():
    # the production catalog key (private half in the catalog repo's GitHub secret); the test key is never this
    assert re.fullmatch(r"[0-9a-f]{64}", trust.PRODUCTION_PUBLIC_KEY_HEX)
    assert trust.public_key() == bytes.fromhex(trust.PRODUCTION_PUBLIC_KEY_HEX)


@pytest.mark.parametrize("name", ENV_NAMES)
def test_env_var_cannot_change_the_trusted_catalog_key(monkeypatch, name):
    before = trust.public_key()
    monkeypatch.setenv(name, "ab" * 32)
    assert trust.public_key() == before == bytes.fromhex(trust.PRODUCTION_PUBLIC_KEY_HEX)


def test_release_wiring_trusts_only_the_production_key():
    # PluginCore's default catalog key source is catalog.trust.public_key, and main.py ships with an empty seam dict
    assert inspect.signature(PC.PluginCore.__init__).parameters["catalog_key"].default is trust.public_key
    main = (DECKY / "main.py").read_text()
    assert re.search(r"^catalog_kw: dict = \{\}", main, re.M)
    # catalog_kw appears exactly twice: the declaration and the `**catalog_kw` in PluginCore(...); nothing assigns it
    assert len(re.findall(r"catalog_kw", main)) == 2


def test_no_callable_can_set_the_catalog_injection_seam():
    """UI -> Plugin callables never touch catalog_kw / catalog_key / catalog_fetch / trust."""
    main = (DECKY / "main.py").read_text()
    callables = main[main.index("# -- callables"):]
    assert not re.search(r"catalog_kw|catalog_key|catalog_fetch|trust\.", callables)


def test_catalog_module_is_shipped_but_no_test_key_is():
    # the catalog package IS part of the plugin bundle
    assert any(t == "py_modules" or t.startswith("py_modules") for t in MZ.TREES) or \
        any(f.startswith("py_modules") for f in MZ.FILES)
    # no test fixture key ships inside py_modules
    assert not list((DECKY / "py_modules").rglob("*TEST-ONLY*"))
