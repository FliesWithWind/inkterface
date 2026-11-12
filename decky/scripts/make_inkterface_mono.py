#!/usr/bin/env python3
"""Derive the bundled "Inkterface Mono" fonts from the original IBM Plex Mono files.

    python3 decky/scripts/make_inkterface_mono.py           # rewrites decky/py_modules/inkterface_core/screens/fonts/InkterfaceMono-*.ttf
    python3 decky/scripts/make_inkterface_mono.py --check   # exit 1 when the committed files differ from a fresh derivation

What changes (and nothing else):
  * all TrueType hinting instructions are removed (fpgm, prep, cvt, gasp, per-glyph programs). FreeType then hints the font with its
    AUTO-hinter, which gives every stem of a face at a given size the same width. Plex's own instructions round each glyph's stem
    alone (l, f, T 2 px next to i, t, h 1 px at 14 px).
  * the font is renamed "Inkterface Mono": the SIL OFL 1.1 reserves the name "Plex" (and "IBM Plex"), so a modified font must not carry it.
    Copyright, licence, designer and trademark records are kept as they are.
The outlines, metrics, kerning and OpenType tables are the original's. Needs fonttools (pinned in decky/fonts-src/requirements.txt) and
is run by hand; the derived files are committed, so the plugin and the idle-font generator need nothing but Pillow.
"""
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
SRC = os.path.join(ROOT, "decky", "fonts-src")
OUT = os.path.join(ROOT, "decky", "py_modules", "inkterface_core", "screens", "fonts")
FAMILY = "Inkterface Mono"
VERSION = "Version 2.3 (derived from IBM Plex Mono 2.3: hinting instructions removed)"
STYLES = {"Regular": "IBMPlexMono-Regular.ttf", "Bold": "IBMPlexMono-Bold.ttf"}


def derive(style):
    from fontTools.ttLib import TTFont
    from fontTools.ttLib.tables import ttProgram

    f = TTFont(os.path.join(SRC, STYLES[style]), recalcTimestamp=False)  # keep head.modified: the output must be reproducible
    for t in ("fpgm", "prep", "cvt ", "gasp", "hdmx", "LTSH", "VDMX"):
        if t in f:
            del f[t]
    for g in f["glyf"].glyphs.values():
        if hasattr(g, "program"):
            g.program = ttProgram.Program()
            g.program.fromBytecode(b"")
    for k in ("maxSizeOfInstructions", "maxStorage", "maxFunctionDefs", "maxInstructionDefs", "maxStackElements", "maxTwilightPoints"):
        setattr(f["maxp"], k, 0)
    full = f"{FAMILY} {style}"
    ps = f"InkterfaceMono-{style}"
    for rec in list(f["name"].names):
        s = {1: FAMILY, 3: f"2.3;{ps}", 4: full, 5: VERSION, 6: ps}.get(rec.nameID)
        if s is not None:
            rec.string = s
    f["name"].setName(f"{FAMILY} is IBM Plex Mono with the TrueType instructions removed, so FreeType's auto-hinter gives all stems the same width.",
                      10, 3, 1, 1033)
    # the OFL reserves "Plex": it may be mentioned (as the origin) only in the copyright, version, description, trademark and licence records, never in a name
    for rec in f["name"].names:
        if "Plex" in rec.toUnicode() and rec.nameID not in (0, 5, 7, 10, 13):
            raise SystemExit(f"name {rec.nameID} still says Plex: {rec.toUnicode()!r}")
    buf = io.BytesIO()
    f.save(buf)
    return buf.getvalue()


def main():
    check = "--check" in sys.argv
    bad = []
    for style in STYLES:
        path = os.path.join(OUT, f"InkterfaceMono-{style}.ttf")
        data = derive(style)
        if check:
            if not os.path.exists(path) or open(path, "rb").read() != data:
                bad.append(path)
        else:
            open(path, "wb").write(data)
            print(f"wrote {path} ({len(data)} bytes)")
    if bad:
        print("stale: " + ", ".join(bad), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
