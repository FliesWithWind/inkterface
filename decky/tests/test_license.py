"""The plugin zip ships decky/LICENSE: it must be the project's MIT text, not the Decky template's placeholder."""
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_plugin_license_is_the_root_mit_license():
    text = (REPO / "decky" / "LICENSE").read_text()
    assert text == (REPO / "LICENSE").read_text()
    assert text.startswith("MIT License") and "Hypothetical Plugin Developer" not in text


def test_package_json_license_is_mit():
    assert json.loads((REPO / "decky" / "package.json").read_text())["license"] == "MIT"
