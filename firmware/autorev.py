import os
import re
import subprocess

# Same grammar as decky/py_modules/inkterface_core/ota/manifest.py (decky/tests/test_fw_version.py keeps them equal).
# Not imported from there: this script runs inside PlatformIO, where the plugin's dependencies are not installed.
SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+[0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*)?$"
)


def _git(*args):
    try:
        r = subprocess.run(["git", *args], capture_output=True, text=True)
    except OSError:  # no git binary (source tarball, minimal container)
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def firmware_version(environ=os.environ, git=_git) -> str:
    """The string this build reports as HELLO.fw_ver.

    Release: INKTERFACE_FW_VERSION, which CI takes from the fw-v<semver> tag. It must equal the signed manifest's
    version, so anything that is not semver fails the build instead of shipping a panel the plugin cannot confirm.
    Dev: 0.0.0-dev+<7-char sha>[.dirty], or 0.0.0-dev+unknown without git. The plugin treats 0.0.0-dev as "unknown
    version" (any release is offered). Never derived from tags.
    """
    ver = (environ.get("INKTERFACE_FW_VERSION") or "").strip()
    if ver:
        if not SEMVER.match(ver):
            raise ValueError(f"INKTERFACE_FW_VERSION={ver!r} is not a semantic version (CI passes the fw-v tag without 'fw-v')")
        return ver
    sha = git("rev-parse", "--short=7", "HEAD") or "unknown"
    dirty = ".dirty" if git("status", "--porcelain", "--untracked-files=no") else ""
    return f"0.0.0-dev+{sha}{dirty}"


try:
    Import("env")  # noqa: F821 - injected by PlatformIO/SCons
except NameError:  # imported by the tests
    env = None

if env is not None:
    version = firmware_version()
    print(f"Firmware version: {version}")
    env.Append(BUILD_FLAGS=[f'-DGIT_REVISION=\\"{version}\\"'])
