"""Helpers for the catalog tests: a TEST-ONLY keypair from a fixed seed, a signed-index builder, package
zips, preview PNGs, and a FakeFetch over a url -> bytes dict. No real network, no real keys on disk."""
from __future__ import annotations

import hashlib
import io
import json
import sys
from pathlib import Path

from PIL import Image

from inkterface_core.catalog import DEFAULT_URL
from inkterface_core.ota import ed25519
from inkterface_core.packages.extract import PackageError

# packages_testlib (build_zip helpers) lives one dir over
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages"))
from packages_testlib import package_zip  # noqa: E402

# A deterministic test-only Ed25519 keypair. NOT the production key; it reaches the client only through
# the Catalog(public_key=...) seam, never bundled.
SEED = hashlib.sha256(b"inkterface-catalog-TEST-ONLY-seed").digest()
PUB = ed25519.publickey(SEED)
OTHER_SEED = hashlib.sha256(b"a different catalog key").digest()
OTHER_PUB = ed25519.publickey(OTHER_SEED)

BASE = DEFAULT_URL


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def preview_png(size=(648, 480), color=0) -> bytes:
    img = Image.new("L", size, color)
    img.putpixel((0, 0), 255)   # a non-flat pixel so it is a real image
    b = io.BytesIO()
    img.save(b, "PNG")
    return b.getvalue()


def entry_dict(pid="demo", version="1.0.0", *, zip_bytes=None, preview=None, has_code=False, has_active=True,
               has_idle=False, min_plugin_version="0.0.1", name=None, author="Tester", description="A screen.",
               **over) -> dict:
    zip_bytes = zip_bytes if zip_bytes is not None else package_zip(pid, version)
    preview = preview if preview is not None else preview_png()
    d = {
        "id": pid, "name": name or f"Demo {pid}", "version": version, "author": author, "description": description,
        "has_code": has_code, "has_active": has_active, "has_idle": has_idle,
        "zip": f"zips/{pid}-{version}.zip", "sha256": sha(zip_bytes), "size": len(zip_bytes),
        "preview": f"previews/{pid}-{version}.png", "preview_sha256": sha(preview),
        "min_plugin_version": min_plugin_version,
    }
    d.update(over)
    return d


def index_doc(entries, *, serial=1000, schema=1, generated="2026-11-11T00:00:00Z") -> dict:
    return {"schema": schema, "serial": serial, "generated": generated,
            "screens": sorted(entries, key=lambda e: e["id"])}


def canonical_bytes(doc: dict) -> bytes:
    """The exact bytes the generator publishes (contract: indent=1, sort_keys, ensure_ascii=False, trailing \\n)."""
    return (json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def sign_index(doc: dict, seed: bytes = SEED) -> tuple[bytes, bytes]:
    raw = canonical_bytes(doc)
    return raw, ed25519.sign(seed, raw)


class FakeFetch:
    """fetch(url, max_bytes, timeout) -> bytes over a url -> bytes|Exception dict. Records calls.

    A missing url raises PackageError (what packages.fetch.urllib_fetch does for a 404-ish failure), and a body
    over max_bytes also raises PackageError, matching the real downloader's contract."""

    def __init__(self, routes=None):
        self.routes: dict[str, object] = dict(routes or {})
        self.calls: list[str] = []
        self.offline = False

    def publish(self, base: str, raw: bytes, sig: bytes, entries_zip_preview):
        self.routes[base + "index.json"] = raw
        self.routes[base + "index.json.sig"] = sig
        for rel, data in entries_zip_preview.items():
            self.routes[base + rel] = data

    def __call__(self, url: str, max_bytes: int, timeout: float) -> bytes:
        self.calls.append(url)
        if self.offline:
            raise PackageError(f"{url}: download failed: offline")
        r = self.routes.get(url)
        if r is None:
            raise PackageError(f"{url}: download failed: HTTP Error 404")
        if isinstance(r, Exception):
            raise r
        if len(r) > max_bytes:
            raise PackageError(f"{url}: download exceeds {max_bytes} bytes")
        return r


CODE_SRC = "import io\nfrom PIL import Image\ndef render(canvas_size, data, settings):\n    b = io.BytesIO(); Image.new('1', canvas_size, 1).save(b, 'PNG'); return b.getvalue()\n"


def make_screen(pid="demo", version="1.0.0", *, has_code=False, has_idle=False, min_plugin_version="0.0.1",
                zip_bytes=None, preview=None, **entry_over):
    """Build matching (entry, zip_bytes, preview_bytes): the entry's sha256/size are computed over the SAME
    bytes the fetch will serve, so the download verifies."""
    if zip_bytes is None:
        doc = None
        if has_idle:
            from packages_testlib import screen_json
            sj = screen_json(pid, version, code=has_code)
            sj["idle"] = {"render_mode": "mono",
                          "widgets": [{"type": "text", "x": 0, "y": 0, "w": 100, "h": 20}]}
            doc = sj
        zip_bytes = package_zip(pid, version, code=(CODE_SRC if has_code else None), doc=doc)
    preview = preview if preview is not None else preview_png()
    entry = entry_dict(pid, version, zip_bytes=zip_bytes, preview=preview, has_code=has_code, has_idle=has_idle,
                       min_plugin_version=min_plugin_version, **entry_over)
    return entry, zip_bytes, preview


def full_fetch(base=BASE, screens=None, *, serial=1000, seed=SEED):
    """A FakeFetch serving a signed index plus one zip + preview per screen. `screens` are (entry, zip, preview)
    tuples from make_screen. Returns (fetch, raw, sig, doc)."""
    screens = screens if screens is not None else [make_screen()]
    entries = [e for e, _z, _p in screens]
    doc = index_doc(entries, serial=serial)
    raw, sig = sign_index(doc, seed)
    f = FakeFetch()
    routes = {}
    for e, z, p in screens:
        routes[e["zip"]] = z
        routes[e["preview"]] = p
    f.publish(base, raw, sig, routes)
    return f, raw, sig, doc
