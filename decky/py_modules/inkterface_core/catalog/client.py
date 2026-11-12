"""The screen-catalog client: fetch + verify the signed index, cache it, install from it, fetch previews.

One place touches the network: `self._fetch(url, max_bytes, timeout) -> bytes` (packages.fetch.urllib_fetch by
default, https-only, raises PackageError). The index never selects a download host -- every URL is
`base + relative path` from the signed entry, so a compromised index cannot redirect a download.

Cache layout (settings_dir/catalog/):
    index.json              the last verified index bytes (verbatim, so the signature still checks)
    index.json.sig          its 64-byte signature
    previews/<id>-<version>.png
All writes are atomic (temp file + os.replace on the same filesystem). Every cache READ re-verifies the
signature and re-checks the serial-rollback rule, so a cache file edited on disk is refused.

Trust is fail-closed: with no bundled catalog key (catalog/trust.py empty), refresh, list and install all
refuse. The TEST-ONLY key reaches this client only through the `public_key` constructor argument.
"""
from __future__ import annotations

import hashlib
import io
import logging
import os
import tempfile
import threading
from pathlib import Path

from PIL import Image

from ..ota.manifest import semver
from ..packages.extract import PackageError
from ..packages.fetch import DEFAULT_TIMEOUT_S, Fetch, urllib_fetch
from . import trust
from .index import (MAX_INDEX_BYTES, MAX_PREVIEW_SIZE, MAX_ZIP_SIZE, CatalogError, Entry, Index,
                    NoCatalogKey, verify_and_parse)

log = logging.getLogger(__name__)

DEFAULT_URL = "https://flieswithwind.github.io/inkterface-screens/"
PREVIEW_SIZE = (648, 480)   # a preview is the panel frame; reject anything else


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Catalog:
    """Thread-safe. All methods raise CatalogError/PackageError on failure; the PluginCore layer turns those into
    the {ok, error} callable convention."""

    def __init__(self, cache_dir: str | Path, url: str = DEFAULT_URL, *, fetch: Fetch = urllib_fetch,
                 public_key=trust.public_key, fetch_timeout: float = DEFAULT_TIMEOUT_S):
        self.dir = Path(cache_dir)
        self.url = url if url.endswith("/") else url + "/"
        self._fetch, self._key, self._timeout = fetch, public_key, fetch_timeout
        self._lock = threading.RLock()   # ponytail: one lock for the whole client; matches PackageStore
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "previews").mkdir(exist_ok=True)

    # -- paths -------------------------------------------------------------------------------------------
    @property
    def _index_file(self) -> Path:
        return self.dir / "index.json"

    @property
    def _sig_file(self) -> Path:
        return self.dir / "index.json.sig"

    def _preview_file(self, pid: str, version: str) -> Path:
        return self.dir / "previews" / f"{pid}-{version}.png"

    def _atomic_write(self, path: Path, data: bytes) -> None:
        fd, tmp = tempfile.mkstemp(prefix=".cat-", suffix=".tmp", dir=self.dir)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # -- key gate ----------------------------------------------------------------------------------------
    def _require_key(self) -> bytes:
        key = self._key()
        if not key:
            raise NoCatalogKey("no catalog signing key is bundled in this plugin build; the screen catalog is disabled")
        return key

    # -- cache read --------------------------------------------------------------------------------------
    def _read_cache(self) -> tuple[Index, int] | None:
        """(verified index, mtime) or None if nothing is cached. Re-verifies the signature every time; a cache file
        tampered with on disk raises CatalogError."""
        key = self._require_key()
        try:
            raw = self._index_file.read_bytes()
            sig = self._sig_file.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as e:
            raise CatalogError(f"cannot read the saved catalog: {e.strerror or e}") from None
        idx = verify_and_parse(raw, sig, key)   # raises if tampered / wrong key / empty key
        try:
            mtime = int(self._index_file.stat().st_mtime)
        except OSError:
            mtime = 0
        return idx, mtime

    def cached(self) -> tuple[Index | None, int | None]:
        """(index, fetched_at). (None, None) when nothing is cached. Raises if the cache fails verification."""
        with self._lock:
            got = self._read_cache()
            return (None, None) if got is None else got

    # -- refresh -----------------------------------------------------------------------------------------
    def refresh(self) -> tuple[Index, int]:
        """Fetch index.json + .sig, verify, apply the serial-rollback rule, save the cache atomically.

        A network failure raises PackageError and the cache is left untouched. A bad signature/schema raises
        CatalogError and the cache is NOT replaced. On success returns (index, fetched_at)."""
        key = self._require_key()
        with self._lock:
            raw = self._fetch(self.url + "index.json", MAX_INDEX_BYTES, self._timeout)
            sig = self._fetch(self.url + "index.json.sig", 256, self._timeout)
            idx = verify_and_parse(raw, sig, key)   # signature BEFORE parse
            prev = None
            try:
                prev = self._read_cache()
            except CatalogError:
                prev = None   # a corrupt cache does not block a fresh, verified index
            if prev is not None and idx.serial < prev[0].serial:
                raise CatalogError(f"the fetched catalog (serial {idx.serial}) is older than the saved catalog "
                                   f"(serial {prev[0].serial}); refusing to roll back")
            self._atomic_write(self._index_file, raw)
            self._atomic_write(self._sig_file, sig)
            try:
                fetched_at = int(self._index_file.stat().st_mtime)
            except OSError:
                fetched_at = 0
            return idx, fetched_at

    # -- entries -----------------------------------------------------------------------------------------
    def entry(self, idx: Index, screen_id: str) -> Entry:
        e = next((e for e in idx.entries if e.id == screen_id), None)
        if e is None:
            raise CatalogError(f"'{screen_id}' is not in the catalog")
        return e

    # -- downloads (base + relative path from the signed entry) ------------------------------------------
    def fetch_zip(self, entry: Entry) -> bytes:
        """Download and verify the package zip for `entry`: size AND sha256 must match the signed index."""
        data = self._fetch(self.url + entry.zip, MAX_ZIP_SIZE, self._timeout)
        if len(data) != entry.size:
            raise CatalogError(f"{entry.id}: downloaded zip is {len(data)} bytes, the catalog says {entry.size}")
        got = sha256_hex(data)
        if got != entry.sha256:
            raise CatalogError(f"{entry.id}: downloaded zip sha256 does not match the catalog")
        return data

    def preview_png(self, entry: Entry) -> bytes:
        """The preview PNG for `entry`, from the on-disk cache if present, else downloaded and cached. Verifies
        size, sha256 (signed) and that the bytes are a 648x480 PNG. Works offline once cached."""
        with self._lock:
            path = self._preview_file(entry.id, entry.version)
            try:
                data = path.read_bytes()
                if len(data) <= MAX_PREVIEW_SIZE and sha256_hex(data) == entry.preview_sha256:
                    return data
            except FileNotFoundError:
                pass
            except OSError:
                pass
            data = self._fetch(self.url + entry.preview, MAX_PREVIEW_SIZE, self._timeout)
            if len(data) > MAX_PREVIEW_SIZE:
                raise CatalogError(f"{entry.id}: preview is larger than {MAX_PREVIEW_SIZE} bytes")
            if sha256_hex(data) != entry.preview_sha256:
                raise CatalogError(f"{entry.id}: preview sha256 does not match the catalog")
            try:
                with Image.open(io.BytesIO(data)) as im:
                    if im.format != "PNG" or im.size != PREVIEW_SIZE:
                        raise CatalogError(f"{entry.id}: preview must be a {PREVIEW_SIZE[0]}x{PREVIEW_SIZE[1]} PNG")
                    im.load()
            except CatalogError:
                raise
            except (OSError, ValueError, Image.DecompressionBombError):
                raise CatalogError(f"{entry.id}: preview is not a readable PNG") from None
            self._atomic_write(path, data)
            return data


def is_newer(catalog_version: str, installed_version: str) -> bool:
    """True when the catalog's version is strictly newer than what is installed (semver)."""
    try:
        return semver(catalog_version) > semver(installed_version)
    except ValueError:
        return False
