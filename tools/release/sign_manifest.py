#!/usr/bin/env python3
"""Make the three release assets from a firmware .bin: image copy, manifest.json, manifest.sig.

    sign_manifest.py build/firmware.bin --version fw-v1.2.3 --key-file key.hex --out-dir dist/
    INKTERFACE_SIGNING_KEY=<64 hex> sign_manifest.py fw.bin --version 1.2.3 --key-env INKTERFACE_SIGNING_KEY ...

Outputs in --out-dir: inkterface-ee05-<ver>.bin, manifest.json, manifest.sig (raw 64-byte
Ed25519 signature over the exact manifest.json bytes). Stdlib only. The manifest is re-parsed
with the plugin's own validator and the signature re-verified before anything is reported
as done, so a release the plugin would reject fails here, not in the field.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "decky", "py_modules"))

from inkterface_core import proto as P  # noqa: E402
from inkterface_core.ota import ed25519, manifest as M  # noqa: E402

ESP_IMAGE_MAGIC = 0xE9


def load_seed(text: str) -> bytes:
    text = text.strip()
    try:
        seed = bytes.fromhex(text)
    except ValueError:
        seed = b""
    if len(seed) != 32:
        raise SystemExit("signing key must be 64 hex characters (the output of gen_keypair.py)")
    return seed


def build(bin_path: str, version: str, seed: bytes, out_dir: str, *, min_plugin_version: str, changelog: str, protocol_version: int) -> dict:
    version = version[4:] if version.startswith("fw-v") else version
    with open(bin_path, "rb") as f:
        image = f.read()
    if not image or image[0] != ESP_IMAGE_MAGIC:
        raise SystemExit(f"{bin_path} is not an ESP32 app image (first byte must be 0xE9)")
    doc = {
        "version": version,
        "protocol_version": protocol_version,
        "sha256": hashlib.sha256(image).hexdigest(),
        "size": len(image),
        "min_plugin_version": min_plugin_version,
        "changelog": changelog,
    }
    raw = (json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    try:
        M.parse_manifest(raw)
    except Exception as e:
        raise SystemExit(f"refusing to publish a manifest the plugin would reject: {e}")
    sig = ed25519.sign(seed, raw)
    assert ed25519.verify(ed25519.publickey(seed), raw, sig), "self-check of the fresh signature failed"
    os.makedirs(out_dir, exist_ok=True)
    shutil.copyfile(bin_path, os.path.join(out_dir, M.asset_name(version)))
    with open(os.path.join(out_dir, "manifest.json"), "wb") as f:
        f.write(raw)
    with open(os.path.join(out_dir, "manifest.sig"), "wb") as f:
        f.write(sig)
    return doc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("bin")
    ap.add_argument("--version", required=True, help="semver, or the tag fw-v<semver>")
    k = ap.add_mutually_exclusive_group(required=True)
    k.add_argument("--key-file", help="file with the private seed as 64 hex chars")
    k.add_argument("--key-env", help="name of an environment variable holding the seed")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--min-plugin-version", default="0.0.1", help="oldest plugin that may install this firmware")
    ap.add_argument("--protocol-version", type=int, default=P.PROTO_VER)
    c = ap.add_mutually_exclusive_group()
    c.add_argument("--changelog", default="")
    c.add_argument("--changelog-file")
    a = ap.parse_args(argv)

    if a.key_file:
        with open(a.key_file) as f:
            seed = load_seed(f.read())
    else:
        seed = load_seed(os.environ.get(a.key_env, ""))
    changelog = a.changelog
    if a.changelog_file:
        with open(a.changelog_file, encoding="utf-8") as f:
            changelog = f.read().strip()
    doc = build(a.bin, a.version, seed, a.out_dir, min_plugin_version=a.min_plugin_version,
                changelog=changelog, protocol_version=a.protocol_version)
    print(json.dumps({k: doc[k] for k in ("version", "protocol_version", "sha256", "size")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
