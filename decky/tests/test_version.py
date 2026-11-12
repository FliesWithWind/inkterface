"""One release version. The VERSION file is the source of truth; everything else must agree with it."""
import json
from pathlib import Path

from inkterface_core.ota import manifest as M

REPO = Path(__file__).resolve().parents[2]
VERSION = (REPO / "VERSION").read_text().strip()


def test_version_file_is_one_semver_line():
    assert (REPO / "VERSION").read_text() == VERSION + "\n"
    M.semver(VERSION)   # raises ValueError if it is not semver


def test_plugin_package_json_agrees():
    # main.py reports this string as the plugin version and OTA compares min_plugin_version against it
    assert json.loads((REPO / "decky" / "package.json").read_text())["version"] == VERSION


def test_firmware_min_plugin_version_is_not_above_this_plugin():
    mpv = (REPO / "firmware" / "min_plugin_version.txt").read_text().strip()
    assert M.semver(mpv) <= M.semver(VERSION)


def test_release_workflows_refuse_a_tag_that_disagrees_with_version_file():
    for wf, prefix in (("firmware-release.yml", "fw-v"), ("plugin-release.yml", "plugin-v")):
        text = (REPO / ".github" / "workflows" / wf).read_text()
        assert f"${{GITHUB_REF_NAME#{prefix}}}" in text and "< VERSION" in text, wf


def test_changelog_and_releasing_doc_name_this_version():
    assert f"\n## [{VERSION}]" in (REPO / "CHANGELOG.md").read_text()
    releasing = (REPO / "docs" / "RELEASING.md").read_text()
    assert f"fw-v{VERSION}" in releasing and f"plugin-v{VERSION}" in releasing
    assert f"INKTERFACE_FW_VERSION={VERSION}" in (REPO / "docs" / "ota.md").read_text()
