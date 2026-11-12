"""Hardened zip extraction + package-level checks. Everything is validated BEFORE anything is installed.

A package zip has exactly one top-level folder, `<id>/`, holding `screen.json`. Only regular files and
directories are accepted. The zip is extracted into a scratch folder (names validated first, files created
exclusively, no symlink is ever created), then `load_screen` validates the content.
"""
from __future__ import annotations

import io
import re
import shutil
import stat
import zipfile
import zlib
from pathlib import Path

from ..screens import ScreenError, load_screen
from ..screens.schema import ID_RE, Screen

MAX_ZIP_BYTES = 5 * 1024 * 1024        # the zip itself (also the download cap)
MAX_TOTAL_BYTES = 20 * 1024 * 1024     # everything unpacked
MAX_FILE_BYTES = 4 * 1024 * 1024       # one unpacked file
MAX_FILES = 200
MAX_RATIO = 100                        # unpacked / packed, checked on entries above RATIO_FLOOR
RATIO_FLOOR = 1024 * 1024
CODE_FILE = "screen.py"
# Other executable-looking files would escape the single sha256 the user approves.
EXEC_SUFFIXES = {".py", ".pyc", ".pyd", ".so", ".dll", ".dylib", ".sh"}

_DRIVE = re.compile(r"[A-Za-z]:")
_CONTROL = re.compile(r"[\x00-\x1f\x7f\\]")


class PackageError(ScreenError):
    """A package, zip or store operation failed. `.errors` holds one message per problem."""


def _check_name(name: str) -> None:
    if _CONTROL.search(name):
        raise PackageError(f"{name!r}: control characters or backslash in file name")
    if name.startswith("/") or _DRIVE.match(name):
        raise PackageError(f"{name!r}: absolute path")
    if any(part in ("", ".", "..") for part in name.removesuffix("/").split("/")):
        raise PackageError(f"{name!r}: path traversal or empty path segment")


def _check_entries(infos: list[zipfile.ZipInfo]) -> str:
    """Validate every entry from its header. Returns the single top-level folder name."""
    if len(infos) > MAX_FILES:
        raise PackageError(f"zip has {len(infos)} entries (max {MAX_FILES})")
    if sum(i.file_size for i in infos) > MAX_TOTAL_BYTES:
        raise PackageError(f"zip unpacks to more than {MAX_TOTAL_BYTES} bytes")
    seen: set[str] = set()
    tops: set[str] = set()
    for i in infos:
        name = i.filename
        _check_name(name)
        key = name.removesuffix("/").casefold()
        if key in seen:
            raise PackageError(f"{name!r}: duplicate entry")
        seen.add(key)
        if i.flag_bits & 0x1:
            raise PackageError(f"{name!r}: encrypted entries are not allowed")
        if i.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise PackageError(f"{name!r}: unsupported compression method {i.compress_type}")
        kind = stat.S_IFMT(i.external_attr >> 16)
        if kind not in (0, stat.S_IFREG, stat.S_IFDIR):
            raise PackageError(f"{name!r}: not a regular file or directory (symlink or device file)")
        if i.file_size > MAX_FILE_BYTES:
            raise PackageError(f"{name!r}: {i.file_size} bytes unpacked (max {MAX_FILE_BYTES})")
        if i.file_size > RATIO_FLOOR and i.file_size > MAX_RATIO * max(i.compress_size, 1):
            raise PackageError(f"{name!r}: compression ratio above {MAX_RATIO}:1 (zip bomb?)")
        tops.add(name.split("/", 1)[0])
        if "/" not in name.removesuffix("/") and not i.is_dir():
            raise PackageError(f"{name!r}: files must live inside the '<id>/' folder")
    if len(tops) != 1:
        raise PackageError(f"zip must contain exactly one top-level folder '<id>/', found {sorted(tops) or 'none'}")
    top = tops.pop()
    if not ID_RE.fullmatch(top):
        raise PackageError(f"{top!r}: top-level folder must be the screen id (lowercase a-z, 0-9, '-')")
    return top


def extract_zip(src: Path | bytes, dest: Path) -> str:
    """Extract a package zip into the existing folder `dest`. Returns the top-level folder name."""
    if isinstance(src, bytes):
        size, fobj = len(src), io.BytesIO(src)
    else:
        try:
            size = src.stat().st_size
        except OSError as e:
            raise PackageError(f"{src}: cannot read: {e.strerror or e}") from None
        fobj = src
    if size > MAX_ZIP_BYTES:
        raise PackageError(f"zip is {size} bytes (max {MAX_ZIP_BYTES})")
    try:
        with zipfile.ZipFile(fobj, metadata_encoding="utf-8") as zf:
            infos = zf.infolist()
            top = _check_entries(infos)
            for i in infos:
                target = dest / i.filename
                if i.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(i) as s, open(target, "xb") as out:   # "x": never overwrite, never follow
                    shutil.copyfileobj(s, out)
    except zipfile.BadZipFile as e:
        raise PackageError(f"not a valid zip: {e}") from None
    except UnicodeDecodeError:
        raise PackageError("zip contains a file name that is not valid UTF-8") from None
    except (OSError, EOFError, NotImplementedError, RuntimeError, zlib.error) as e:
        raise PackageError(f"zip cannot be extracted: {e}") from None
    return top


def check_package(screen: Screen) -> None:
    """Rules on top of `load_screen` for shareable packages: the code tier is one `screen.py`, nothing else runs."""
    errs = []
    files = {p.relative_to(screen.root).as_posix() for p in screen.root.rglob("*") if p.is_file()}
    code = (screen.active or {}).get("code")
    if code is not None and code != CODE_FILE:
        errs.append(f"screen.json: active.code: must be '{CODE_FILE}' in a shareable package")
    if code is None and CODE_FILE in files:
        errs.append(f"{CODE_FILE}: not referenced by screen.json active.code")
    for f in sorted(files):
        if f != CODE_FILE and Path(f).suffix.lower() in EXEC_SUFFIXES:
            errs.append(f"{f}: executable file types are not allowed (only {CODE_FILE})")
    if errs:
        raise PackageError(errs)


def load_zip(src: Path | bytes, workdir: Path) -> Screen:
    """Extract into `workdir` and validate. Raises PackageError/ScreenError naming the file and field."""
    top = extract_zip(src, workdir)
    try:
        screen = load_screen(workdir / top)
    except ScreenError as e:
        raise PackageError(e.errors) from None
    check_package(screen)
    return screen

