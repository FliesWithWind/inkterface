"""The TEST-ONLY signing key used by decky/tools/hw_e2e.py and the plugin tests reaches the updater only through the
`main.firmware_kw` code seam. A release build (the plugin zip as shipped, no test code) must trust the production key
and nothing else: no environment variable, config file or UI call can add a key."""
import inspect
import re
import sys
from pathlib import Path

import pytest

DECKY = Path(__file__).resolve().parents[1]
CORE = DECKY / "py_modules" / "inkterface_core"
sys.path.insert(0, str(DECKY / "scripts"))

import make_zip as MZ  # noqa: E402
from inkterface_core import firmware as FW  # noqa: E402
from inkterface_core.ota import trust  # noqa: E402

SHIPPED_TRUST_CODE = [DECKY / "main.py", CORE / "firmware.py", CORE / "plugin_core.py", *sorted((CORE / "ota").glob("*.py"))]
ENV_NAMES = ["INKTERFACE_TEST_PUBKEY", "INKTERFACE_TEST_KEY", "INKTERFACE_PUBLIC_KEY", "INKTERFACE_OTA_PUBKEY",
             "INKTERFACE_SIGNING_KEY", "INKTERFACE_FW_PUBKEY", "PUBLIC_KEY", "TEST_PUBKEY"]


def test_no_shipped_trust_code_reads_the_environment():
    bad = [f.name for f in SHIPPED_TRUST_CODE if re.search(r"os\.environ|getenv|environ\[|sys\.argv", f.read_text())]
    assert len(SHIPPED_TRUST_CODE) >= 8 and bad == []


@pytest.mark.parametrize("name", ENV_NAMES)
def test_env_var_cannot_change_the_trusted_key(monkeypatch, name):
    before = trust.public_key()
    monkeypatch.setenv(name, "ab" * 32)
    assert trust.public_key() == before == (bytes.fromhex(trust.PRODUCTION_PUBLIC_KEY_HEX) if trust.PRODUCTION_PUBLIC_KEY_HEX else None)


def test_release_wiring_trusts_only_the_production_key():
    # the updater's default key source is trust.public_key, and main.py ships with an empty injection dict
    assert inspect.signature(FW.FirmwareManager.__init__).parameters["public_key"].default is trust.public_key
    main = (DECKY / "main.py").read_text()
    assert re.search(r"^firmware_kw: dict = \{\}", main, re.M)
    assert len(re.findall(r"firmware_kw", main)) == 2   # the declaration and the `**firmware_kw` in PluginCore(...): nothing assigns it


def test_no_callable_can_set_the_injection_seam():
    """UI -> Plugin callables never touch firmware_kw / public_key / fetch."""
    main = (DECKY / "main.py").read_text()
    callables = main[main.index("# -- callables"):]
    assert not re.search(r"firmware_kw|public_key|fw_fetch|trust\.", callables)


def test_test_tooling_is_not_in_the_release_zip():
    assert "tools" not in MZ.TREES and not any(f.startswith("tools") for f in MZ.FILES)
    assert not list((DECKY / "py_modules").rglob("hw_e2e*"))
    assert (DECKY / "tools" / "hw_e2e.py").is_file()   # it exists, it just is not a member of FILES/TREES
