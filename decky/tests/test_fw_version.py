"""firmware/autorev.py: what a build reports as HELLO.fw_ver, and that the plugin reads both kinds correctly."""
import importlib.util
import os
import re
import subprocess
from pathlib import Path

import pytest

from inkterface_core.ota import manifest as M

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("autorev", REPO / "firmware" / "autorev.py")
autorev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(autorev)   # outside PlatformIO `Import` does not exist: the script must still import cleanly


def fake_git(sha="308c457", dirty=False):
    def git(*args):
        if args[0] == "rev-parse":
            return sha
        if args[0] == "status":
            return " M firmware/x.cpp" if dirty else ""
        raise AssertionError(args)
    return git


def test_same_semver_grammar_as_the_plugin():
    assert autorev.SEMVER.pattern == M._SEMVER.pattern


def test_dev_build_is_0_0_0_dev_plus_sha():
    assert autorev.firmware_version({}, fake_git()) == "0.0.0-dev+308c457"


def test_dirty_tree_is_marked():
    assert autorev.firmware_version({}, fake_git(dirty=True)) == "0.0.0-dev+308c457.dirty"


@pytest.mark.parametrize("sha", [None, ""])
def test_no_git_or_no_repo_is_unknown_not_a_crash(sha):
    assert autorev.firmware_version({}, lambda *a: sha) == "0.0.0-dev+unknown"


def test_empty_env_is_a_dev_build():
    assert autorev.firmware_version({"INKTERFACE_FW_VERSION": "  "}, fake_git()) == "0.0.0-dev+308c457"


@pytest.mark.parametrize("ver", ["1.0.0", "1.2.3-rc.1", "10.20.30", "1.0.0-beta+exp.sha.5114f85"])
def test_release_takes_the_version_from_the_env_unchanged(ver):
    assert autorev.firmware_version({"INKTERFACE_FW_VERSION": ver}, lambda *a: pytest.fail("git must not be asked")) == ver


@pytest.mark.parametrize("bad", ["fw-v1.0.0", "v1.0.0", "1.0", "1.0.0.0", "latest", "01.0.0", "1.0.0; reboot", '1.0.0"'])
def test_release_with_a_bad_version_fails_the_build(bad):
    with pytest.raises(ValueError, match="not a semantic version"):
        autorev.firmware_version({"INKTERFACE_FW_VERSION": bad}, fake_git())


def test_tags_are_never_consulted(tmp_path):
    """The version never comes from `git describe`: a stray tag must not leak into HELLO.fw_ver."""
    def run(*a):
        return subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True, text=True)

    run("init", "-q")
    run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x")
    run("tag", "v1-old")
    run("tag", "fw-v9.9.9")
    old = os.getcwd()
    os.chdir(tmp_path)
    try:
        v = autorev.firmware_version({})
    finally:
        os.chdir(old)
    assert re.fullmatch(r"0\.0\.0-dev\+[0-9a-f]{7}", v), v


def test_dev_string_fits_hello_and_the_c_define():
    v = autorev.firmware_version({}, fake_git(sha="a" * 7, dirty=True))
    assert len(v) <= 64 and re.fullmatch(r"[0-9A-Za-z.+-]+", v)   # HELLO fw_ver limit; no shell/C escaping needed


@pytest.mark.parametrize("dev", ["0.0.0-dev+308c457", "0.0.0-dev+308c457.dirty", "0.0.0-dev+unknown"])
def test_what_autorev_emits_for_dev_builds_is_a_dev_build_for_the_plugin(dev):
    assert autorev.SEMVER.match(dev) and M.installed_version(dev) is None


def test_firmware_default_define_matches():
    src = (REPO / "firmware" / "src" / "ee05" / "main.cpp").read_text()
    m = re.search(r'#define GIT_REVISION "([^"]+)"', src)
    assert m and M.installed_version(m.group(1)) is None and "gUNKNOWN" not in src
