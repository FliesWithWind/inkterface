"""Screen-catalog index: strict schema, signature-checked before parse.

Trust rule (same shape as ota/manifest.verify_and_parse): the ed25519 signature is checked over the
EXACT bytes of index.json BEFORE anything is parsed. Nothing downstream ever sees unsigned data.

The index is a flat list of screens (one version per screen), sorted by id with unique ids. Each entry's
zip/preview path must match `^(zips|previews)/<id>-<version>\\.(zip|png)$` with the entry's own id/version,
so a signed index can never point a download at an arbitrary path or host: the client only ever joins
`base + relative path`.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from ..ota import ed25519
from ..ota.manifest import semver
from ..screens.schema import ID_RE, SEMVER_RE

SCHEMA_VERSION = 1
MAX_INDEX_BYTES = 512 * 1024
MAX_SCREENS = 500
SIG_LEN = 64  # raw ed25519 signature, like ota manifest.sig
MAX_ZIP_SIZE = 5 * 1024 * 1024       # packages.extract.MAX_ZIP_BYTES
MAX_PREVIEW_SIZE = 256 * 1024

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_NAME = re.compile(r"^[^\x00-\x1f\x7f]{1,200}$")   # name/author/description: printable, bounded
_TOP_KEYS = {"schema", "serial", "generated", "screens"}
# ponytail: entry keys are a fixed set; no optional/forward-compat keys (unknown keys rejected, strict).
_ENTRY_KEYS = {"id", "name", "version", "author", "description", "has_code", "has_active", "has_idle",
               "zip", "sha256", "size", "preview", "preview_sha256", "min_plugin_version"}


class CatalogError(Exception):
    """A catalog index failed to verify or parse. str(e) is safe to show the user (it may name a URL, never a secret)."""


class NoCatalogKey(CatalogError):
    """No catalog signing key is bundled: refuse every read/install (fail closed)."""


@dataclass(frozen=True)
class Entry:
    id: str
    name: str
    version: str
    author: str
    description: str
    has_code: bool
    has_active: bool
    has_idle: bool
    zip: str
    sha256: str
    size: int
    preview: str
    preview_sha256: str
    min_plugin_version: str


@dataclass(frozen=True)
class Index:
    schema: int
    serial: int
    generated: str
    entries: tuple[Entry, ...]


def _no_dups(pairs):
    d = {}
    for k, v in pairs:
        if k in d:
            raise CatalogError(f"duplicate key {k!r} in index.json")
        d[k] = v
    return d


def _int(d: dict, key: str, where: str) -> int:
    v = d.get(key)
    if type(v) is not int:  # bool is an int subclass: reject it
        raise CatalogError(f"{where}{key} must be an integer")
    return v


def _str(d: dict, key: str, where: str, *, pattern=_NAME) -> str:
    v = d.get(key)
    if not isinstance(v, str) or not pattern.match(v):
        raise CatalogError(f"{where}{key} must be a string")
    return v


def _bool(d: dict, key: str, where: str) -> bool:
    v = d.get(key)
    if not isinstance(v, bool):
        raise CatalogError(f"{where}{key} must be true or false")
    return v


def _path(d: dict, key: str, where: str, folder: str, suffix: str, pid: str, version: str) -> str:
    v = d.get(key)
    want = f"{folder}/{pid}-{version}{suffix}"
    if not isinstance(v, str) or v != want:
        raise CatalogError(f"{where}{key} must be {want!r}")
    return v


def _parse_entry(e, i: int) -> Entry:
    where = f"screens[{i}]."
    if not isinstance(e, dict):
        raise CatalogError(f"screens[{i}] must be an object")
    extra = sorted(set(e) - _ENTRY_KEYS)
    if extra:
        raise CatalogError(f"{where}unknown key {extra[0]!r}")
    missing = sorted(_ENTRY_KEYS - set(e))
    if missing:
        raise CatalogError(f"{where}missing key {missing[0]!r}")
    pid = _str(e, "id", where, pattern=re.compile(f"^(?:{ID_RE.pattern})$"))
    if len(pid) > 64:
        raise CatalogError(f"{where}id is too long")
    version = _str(e, "version", where, pattern=re.compile(f"^(?:{SEMVER_RE.pattern})$"))
    try:
        semver(version)
    except ValueError:
        raise CatalogError(f"{where}version is not a semantic version") from None
    try:
        semver(_str(e, "min_plugin_version", where, pattern=re.compile(f"^(?:{SEMVER_RE.pattern})$")))
    except ValueError:
        raise CatalogError(f"{where}min_plugin_version is not a semantic version") from None
    size = _int(e, "size", where)
    if not 1 <= size <= MAX_ZIP_SIZE:
        raise CatalogError(f"{where}size {size} outside 1..{MAX_ZIP_SIZE}")
    sha = _str(e, "sha256", where, pattern=_SHA256)
    preview_sha = _str(e, "preview_sha256", where, pattern=_SHA256)
    return Entry(
        id=pid,
        name=_str(e, "name", where),
        version=version,
        author=_str(e, "author", where),
        description=_str(e, "description", where),
        has_code=_bool(e, "has_code", where),
        has_active=_bool(e, "has_active", where),
        has_idle=_bool(e, "has_idle", where),
        zip=_path(e, "zip", where, "zips", ".zip", pid, version),
        sha256=sha,
        size=size,
        preview=_path(e, "preview", where, "previews", ".png", pid, version),
        preview_sha256=preview_sha,
        min_plugin_version=e["min_plugin_version"],
    )


def parse_index(raw: bytes) -> Index:
    """Schema check only. Call `verify_and_parse` unless the signature is already verified."""
    if len(raw) > MAX_INDEX_BYTES:
        raise CatalogError(f"index is larger than {MAX_INDEX_BYTES} bytes")
    try:
        d = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_dups)
    except (UnicodeDecodeError, ValueError) as e:
        if isinstance(e, CatalogError):
            raise
        raise CatalogError(f"index is not valid JSON: {e}") from None
    if not isinstance(d, dict):
        raise CatalogError("index must be a JSON object")
    extra = sorted(set(d) - _TOP_KEYS)
    if extra:
        raise CatalogError(f"unknown top-level key {extra[0]!r}")
    if d.get("schema") != SCHEMA_VERSION:
        raise CatalogError(f"index schema must be {SCHEMA_VERSION}")
    serial = _int(d, "serial", "")
    if serial < 0:
        raise CatalogError("serial must be >= 0")
    generated = _str(d, "generated", "", pattern=re.compile(r"^[0-9A-Za-z:\-.+ TZ]{1,40}$"))
    screens = d.get("screens")
    if not isinstance(screens, list):
        raise CatalogError("screens must be a list")
    if len(screens) > MAX_SCREENS:
        raise CatalogError(f"index has {len(screens)} screens (max {MAX_SCREENS})")
    entries = tuple(_parse_entry(e, i) for i, e in enumerate(screens))
    ids = [e.id for e in entries]
    if len(set(ids)) != len(ids):
        dup = next(x for x in ids if ids.count(x) > 1)
        raise CatalogError(f"duplicate screen id {dup!r}")
    # Decision: the publisher sorts by id (indent=1, sort_keys on the generator side); an out-of-order
    # index is a malformed build, so refuse it rather than silently re-sorting.
    if ids != sorted(ids):
        raise CatalogError("screens must be sorted by id")
    return Index(SCHEMA_VERSION, serial, generated, entries)


def verify_and_parse(raw: bytes, sig: bytes, public_key: bytes | None) -> Index:
    if not public_key:
        raise NoCatalogKey("no catalog signing key is bundled in this plugin build; the screen catalog is disabled")
    if len(sig) != SIG_LEN:
        raise CatalogError(f"index.json.sig must be {SIG_LEN} raw bytes, got {len(sig)}")
    if not ed25519.verify(public_key, raw, sig):
        raise CatalogError("catalog index signature does not match the bundled catalog key")
    return parse_index(raw)
