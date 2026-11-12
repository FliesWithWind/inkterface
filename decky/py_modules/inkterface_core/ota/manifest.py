"""Release manifest: semver, strict parse, signature check, install policy.

Trust rule: `verify_and_parse` checks the ed25519 signature over the EXACT manifest bytes
before it parses anything. Nothing downstream ever sees unsigned data.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .. import proto as P
from . import ed25519
from .errors import BadManifest, BadSignature, Downgrade, NoTrustKey, PluginTooOld, ProtocolMismatch

MAX_IMAGE = 0x330000  # one OTA slot, firmware/partitions.csv
MAX_MANIFEST = 64 * 1024
MAX_CHANGELOG = 8000
SIG_LEN = 64  # manifest.sig is the raw 64-byte ed25519 signature, no encoding

_SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+[0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*)?$"
)
_DEV = re.compile(r"^0\.0\.0-dev(?:\+|$)")  # what a dev build reports; never a release version
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def semver(s: str) -> tuple:
    """Sortable key for a SemVer 2.0 string (build metadata ignored). ValueError if invalid."""
    m = _SEMVER.match(s) if isinstance(s, str) else None
    if not m:
        raise ValueError(f"not a semantic version: {s!r}")
    pre = m.group(4)
    if pre is None:
        tail = (1, ())  # a release outranks any of its pre-releases
    else:
        tail = (0, tuple((0, int(i), "") if i.isdigit() else (1, 0, i) for i in pre.split(".")))
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), tail)


def is_prerelease(s: str) -> bool:
    """True for "1.0.0-beta.1", False for "1.0.0". ValueError if not semver."""
    return semver(s)[3][0] == 0


def installed_version(fw_ver: str) -> str | None:
    """Semver of the running firmware from HELLO.fw_ver, or None for a dev build.

    Release images report their bare version ("1.2.0"); a tag-style "fw-v1.2.0" is accepted.
    Dev builds report "0.0.0-dev+<sha>[.dirty]" (firmware/autorev.py); older ones "gABC123-DIRTY". Both, and
    anything else that is not semver, are dev builds: policy cannot compare, any release is offered.
    """
    v = fw_ver[4:] if fw_ver.startswith("fw-v") else fw_ver
    try:
        semver(v)
    except ValueError:
        return None
    return None if _DEV.match(v) else v


def asset_name(version: str) -> str:
    return f"inkterface-ee05-{version}.bin"


@dataclass(frozen=True)
class Manifest:
    version: str
    protocol_version: int
    sha256: str
    size: int
    min_plugin_version: str
    changelog: str = ""

    @property
    def asset(self) -> str:
        return asset_name(self.version)


def _no_dups(pairs):
    d = {}
    for k, v in pairs:
        if k in d:
            raise BadManifest(f"duplicate key {k!r}")
        d[k] = v
    return d


def _int(d: dict, key: str) -> int:
    v = d.get(key)
    if type(v) is not int:  # bool is an int subclass: reject it
        raise BadManifest(f"{key} must be an integer")
    return v


def _semver_field(d: dict, key: str) -> str:
    v = d.get(key)
    try:
        semver(v)
    except ValueError:
        raise BadManifest(f"{key} is not a semantic version") from None
    return v


def parse_manifest(raw: bytes) -> Manifest:
    """Schema check only. Call `verify_and_parse` unless you already verified the signature."""
    if len(raw) > MAX_MANIFEST:
        raise BadManifest("manifest too large")
    try:
        d = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_dups)
    except (UnicodeDecodeError, ValueError) as e:
        raise BadManifest(f"manifest is not valid JSON: {e}") from None
    if not isinstance(d, dict):
        raise BadManifest("manifest must be a JSON object")
    version = _semver_field(d, "version")
    min_plugin = _semver_field(d, "min_plugin_version")
    proto = _int(d, "protocol_version")
    size = _int(d, "size")
    if proto < 0:
        raise BadManifest("protocol_version must be >= 0")
    if not 1 <= size <= MAX_IMAGE:
        raise BadManifest(f"size {size} outside 1..{MAX_IMAGE}")
    sha = d.get("sha256")
    if not isinstance(sha, str) or not _SHA256.match(sha):
        raise BadManifest("sha256 must be 64 lowercase hex characters")
    log = d.get("changelog", "")
    if not isinstance(log, str) or len(log) > MAX_CHANGELOG:
        raise BadManifest("changelog must be a string of at most 8000 characters")
    return Manifest(version, proto, sha, size, min_plugin, log)


def verify_and_parse(raw: bytes, sig: bytes, public_key: bytes | None) -> Manifest:
    if not public_key:
        raise NoTrustKey("no release signing key is bundled in this plugin build; updates are disabled")
    if len(sig) != SIG_LEN:
        raise BadSignature(f"manifest.sig must be {SIG_LEN} raw bytes, got {len(sig)}")
    if not ed25519.verify(public_key, raw, sig):
        raise BadSignature("manifest signature does not match the bundled release key")
    return parse_manifest(raw)


def check_policy(m: Manifest, *, plugin_version: str, installed: str | None, proto_version: int = P.PROTO_VER) -> bool:
    """True = offer this release. False = panel already runs it. Raises when it must not be installed."""
    if installed is not None:
        cur, new = semver(installed), semver(m.version)
        if new < cur:
            raise Downgrade(f"release {m.version} is older than the installed {installed}")
        if new == cur:
            return False
    if m.protocol_version != proto_version:
        raise ProtocolMismatch(f"firmware speaks protocol {m.protocol_version}, this plugin speaks {proto_version}")
    if semver(m.min_plugin_version) > semver(plugin_version):
        raise PluginTooOld(f"firmware {m.version} needs plugin >= {m.min_plugin_version}, this is {plugin_version}")
    return True
