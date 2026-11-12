"""Zip a screen folder the way the importer wants it: `<id>/` root, plain files, fixed timestamps."""
from __future__ import annotations

import os
import tempfile
import zipfile
from pathlib import Path

from ..screens import load_screen
from .extract import PackageError, load_zip

_SKIP_DIRS = {"__pycache__"}
_EPOCH = (1980, 1, 1, 0, 0, 0)   # reproducible output


def _files(folder: Path):
    for dirpath, dirs, files in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS and not d.startswith("."))
        for name in sorted(files):
            p = Path(dirpath) / name
            if p.suffix in (".zip", ".pyc") or name.startswith("."):
                continue
            yield p
        for name in dirs:
            if (Path(dirpath) / name).is_symlink():
                raise PackageError(f"{Path(dirpath) / name}: symlinks are not allowed in a package")


def make_zip(folder: str | Path, out: str | Path | None = None) -> Path:
    """Validate `folder` as a package, write `<out>` (default `<folder>/../<id>-<version>.zip`) and re-import it as a check."""
    folder = Path(folder).resolve()
    screen = load_screen(folder)
    out = Path(out) if out else folder.parent / f"{screen.id}-{screen.version}.zip"
    try:
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for p in _files(folder):
                if p.is_symlink():
                    raise PackageError(f"{p}: symlinks are not allowed in a package")
                info = zipfile.ZipInfo(f"{screen.id}/{p.relative_to(folder).as_posix()}", _EPOCH)
                info.external_attr = 0o100644 << 16
                info.create_system = 3
                info.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(info, p.read_bytes())
        with tempfile.TemporaryDirectory() as td:
            load_zip(out, Path(td))
    except BaseException:
        out.unlink(missing_ok=True)
        raise
    return out
