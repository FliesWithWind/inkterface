"""Signed screen catalog: verify + cache a published index of community screens, install from it, fetch previews.

Same trust shape as ota/: a bundled ed25519 public key (catalog/trust.py, separate from the firmware key),
signature checked before parse, fail-closed when no key is bundled. See docs/screen-catalog.md.
"""
from .client import DEFAULT_URL, Catalog, is_newer
from .index import CatalogError, Entry, Index, NoCatalogKey, parse_index, verify_and_parse

__all__ = ["Catalog", "CatalogError", "DEFAULT_URL", "Entry", "Index", "NoCatalogKey", "is_newer",
           "parse_index", "verify_and_parse"]
