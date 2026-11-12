"""Zip extraction attacks: each one must be refused, name the entry, and leave nothing behind."""
import io
import json
import os
import stat
import struct
import warnings
import zipfile

import pytest
from packages_testlib import GOOD_CODE, package_files, package_zip, raw_zip, screen_json, with_json, zinfo, zip_bytes

from inkterface_core.packages import PackageError
from inkterface_core.packages import extract as X
from inkterface_core.screens import ScreenError


@pytest.fixture
def dest(tmp_path):
    d = tmp_path / "dest"
    d.mkdir()
    return d


def refuse(data, dest, match):
    with pytest.raises(PackageError, match=match):
        X.load_zip(data, dest)
    return dest


def test_good_package_loads(dest):
    s = X.load_zip(package_zip(), dest)
    assert s.id == "demo-pkg" and (dest / "demo-pkg" / "screen.json").is_file()


def test_good_zip_from_path(dest, write_zip):
    assert X.load_zip(write_zip(package_zip()), dest).version == "1.0.0"


def test_unicode_names_are_fine(dest):
    z = zip_bytes(package_files(extra={"assets/ünï.txt": b"x"}), top="demo-pkg")
    assert X.load_zip(z, dest).id == "demo-pkg" and (dest / "demo-pkg" / "assets" / "ünï.txt").is_file()


# ---- the attacks ---------------------------------------------------------------------------------------
def test_zip_slip_dotdot(dest, tmp_path):
    z = with_json([(zinfo("demo-pkg/../../evil.txt"), b"pwned")])
    refuse(z, dest, r"evil\.txt.*path traversal")
    assert not (tmp_path / "evil.txt").exists() and not (dest.parent / "evil.txt").exists()


def test_zip_slip_nested_dotdot(dest):
    refuse(with_json([(zinfo("demo-pkg/a/../../../x"), b"")]), dest, "path traversal")


def test_absolute_path(dest):
    refuse(with_json([(zinfo("/tmp/evil.txt"), b"")]), dest, r"/tmp/evil\.txt.*absolute path")


def test_windows_drive_path(dest):
    refuse(with_json([(zinfo("C:/evil.txt"), b"")]), dest, "absolute path")


def test_backslash_in_name(dest):
    refuse(with_json([(zinfo("demo-pkg\\..\\evil.txt"), b"")]), dest, "backslash")


def test_control_character_in_name(dest):
    refuse(with_json([(zinfo("demo-pkg/a\x01b"), b"")]), dest, "control characters")


@pytest.mark.parametrize("name", ["demo-pkg//a", "demo-pkg/./a"])
def test_empty_or_dot_segment(dest, name):
    refuse(with_json([(zinfo(name), b"")]), dest, "path traversal or empty path segment")


def test_symlink_entry(dest):
    z = with_json([(zinfo("demo-pkg/link", 0o777, stat.S_IFLNK), b"/etc/passwd")])
    refuse(z, dest, r"link.*symlink or device")
    assert not any(p.is_symlink() for p in dest.rglob("*"))


@pytest.mark.parametrize("kind", [stat.S_IFCHR, stat.S_IFBLK, stat.S_IFIFO, stat.S_IFSOCK])
def test_device_and_special_files(dest, kind):
    refuse(with_json([(zinfo("demo-pkg/dev", 0o666, kind), b"")]), dest, "symlink or device")


def test_duplicate_entry_names(dest):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")   # zipfile warns about the duplicate it is asked to write
        z = with_json([(zinfo("demo-pkg/a.txt"), b"1"), (zinfo("demo-pkg/a.txt"), b"2")])
    refuse(z, dest, "duplicate entry")


def test_case_insensitive_duplicate(dest):
    refuse(with_json([(zinfo("demo-pkg/A.txt"), b"1"), (zinfo("demo-pkg/a.txt"), b"2")]), dest, "duplicate entry")


def test_zip_bomb_by_ratio(dest):
    z = with_json([(zinfo("demo-pkg/zeros.bin"), b"\0" * (3 * 1024 * 1024))])
    assert len(z) < 20_000
    refuse(z, dest, r"zeros\.bin.*compression ratio")


def test_zip_bomb_by_total_size(dest):
    infos = [(zinfo(f"demo-pkg/z{i}.bin"), b"\0" * (3 * 1024 * 1024)) for i in range(8)]
    refuse(with_json(infos), dest, "unpacks to more than")


def test_single_file_too_large(dest):
    refuse(with_json([(zinfo("demo-pkg/big.bin"), b"\0" * (5 * 1024 * 1024))]), dest, r"big\.bin.*max")


def test_too_many_files(dest):
    infos = [(zinfo(f"demo-pkg/f{i}.txt"), b"") for i in range(X.MAX_FILES + 1)]
    refuse(with_json(infos), dest, f"{X.MAX_FILES + 2} entries")


def test_oversized_zip_bytes(dest):
    refuse(b"x" * (X.MAX_ZIP_BYTES + 1), dest, "zip is .* bytes")


def test_oversized_zip_file(dest, write_zip):
    refuse(write_zip(b"x" * (X.MAX_ZIP_BYTES + 1)), dest, "zip is .* bytes")


def test_non_utf8_name(dest):
    z = with_json([(zinfo("demo-pkg/aXb.txt"), b"")]).replace(b"aXb.txt", b"a\xffb.txt")
    refuse(z, dest, "not valid UTF-8")


def test_header_lies_about_size(dest):
    z = bytearray(raw_zip([(zinfo("demo-pkg/screen.json"), package_files()["screen.json"]),
                           (zinfo("demo-pkg/a.bin"), b"A" * 1000)]))
    central = z.rindex(b"PK\x01\x02", 0, z.rindex(b"a.bin"))
    local = z.rindex(b"PK\x03\x04", 0, z.index(b"a.bin"))
    for pos, off in ((central, 24), (local, 22)):   # uncompressed size field
        z[pos + off:pos + off + 4] = struct.pack("<I", 10)
    refuse(bytes(z), dest, "not a valid zip|cannot be extracted")


def test_encrypted_entry(dest):
    z = bytearray(with_json([(zinfo("demo-pkg/secret.txt"), b"x")]))
    for sig, off in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):   # general purpose flag, bit 0 = encrypted
        pos = z.rindex(sig, 0, z.index(b"secret.txt") if sig == b"PK\x03\x04" else z.rindex(b"secret.txt"))
        z[pos + off] |= 1
    refuse(bytes(z), dest, "encrypted")


def test_unsupported_compression(dest):
    zi = zinfo("demo-pkg/a.bin")
    zi.compress_type = zipfile.ZIP_BZIP2
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(zinfo("demo-pkg/screen.json"), package_files()["screen.json"])
        z.writestr(zi, b"abc")
    refuse(buf.getvalue(), dest, "unsupported compression")


def test_not_a_zip(dest):
    refuse(b"definitely not a zip", dest, "not a valid zip")


def test_unreadable_path(dest, tmp_path):
    refuse(tmp_path / "missing.zip", dest, "cannot read")


def test_corrupt_deflate_stream(dest):
    z = bytearray(package_zip(extra={"assets/a.bin": os.urandom(2000)}))
    pos = z.index(b"assets/a.bin") + len(b"assets/a.bin") + 100
    z[pos:pos + 50] = b"\xff" * 50
    refuse(bytes(z), dest, "not a valid zip|cannot be extracted")


# ---- structure -------------------------------------------------------------------------------------------
def test_file_at_zip_root(dest):
    refuse(raw_zip([(zinfo("screen.json"), b"{}")]), dest, "inside the '<id>/' folder")


def test_two_top_level_folders(dest):
    z = raw_zip([(zinfo("a/screen.json"), b"{}"), (zinfo("b/screen.json"), b"{}")])
    refuse(z, dest, "exactly one top-level folder")


def test_empty_zip(dest):
    refuse(raw_zip([]), dest, "found none")


def test_top_folder_must_be_an_id(dest):
    refuse(raw_zip([(zinfo("My Screen/screen.json"), b"{}")]), dest, "must be the screen id")


def test_directory_entries_are_created(dest):
    z = raw_zip([(zinfo("demo-pkg/assets/", 0o755, stat.S_IFDIR), b""),
                 (zinfo("demo-pkg/screen.json"), package_files()["screen.json"])])
    X.load_zip(z, dest)
    assert (dest / "demo-pkg" / "assets").is_dir()


def test_file_and_directory_clash(dest):
    z = with_json([(zinfo("demo-pkg/a"), b"file"), (zinfo("demo-pkg/a/b"), b"x")])
    refuse(z, dest, "cannot be extracted")


# ---- validation: errors name file and field ----------------------------------------------------------
def test_missing_screen_json(dest):
    refuse(raw_zip([(zinfo("demo-pkg/readme.txt"), b"hi")]), dest, "screen.json: not found")


def test_invalid_field_is_named(dest):
    doc = screen_json()
    doc["active"]["widgets"][0]["x"] = 700
    with pytest.raises(ScreenError) as e:
        X.load_zip(package_zip(doc=doc), dest)
    assert any(m.startswith("screen.json: active.widgets[0].") for m in e.value.errors), e.value.errors


def test_id_must_match_folder(dest):
    z = zip_bytes(package_files(pid="other"), top="demo-pkg")
    refuse(z, dest, "id: 'other' must match the folder name 'demo-pkg'")


def test_bad_json(dest):
    z = raw_zip([(zinfo("demo-pkg/screen.json"), b"{not json")])
    refuse(z, dest, "screen.json: invalid JSON")


def test_bad_version(dest):
    with pytest.raises(ScreenError, match="screen.json: version"):
        X.load_zip(package_zip(version="one"), dest)


def test_missing_asset_is_named(dest):
    doc = screen_json()
    doc["active"]["widgets"] = [{"type": "image", "x": 0, "y": 0, "w": 10, "h": 10, "src": "assets/nope.png"}]
    with pytest.raises(ScreenError, match=r"nope\.png|assets"):
        X.load_zip(package_zip(doc=doc), dest)


# ---- code-tier rules --------------------------------------------------------------------------------------
def test_code_package_ok(dest):
    s = X.load_zip(package_zip(code=GOOD_CODE), dest)
    assert s.code_path.name == "screen.py"


@pytest.mark.parametrize("name", ["helper.py", "lib/x.PY", "a.so", "a.pyc", "a.pyd", "a.dll", "a.dylib", "run.sh"])
def test_other_executable_files_refused(dest, name):
    refuse(package_zip(code=GOOD_CODE, extra={name: b"x"}), dest, rf"{name}: executable file types")


def test_screen_py_without_declaration(dest):
    refuse(package_zip(extra={"screen.py": GOOD_CODE}), dest, "screen.py: not referenced by screen.json")


def test_code_path_must_be_screen_py(dest):
    doc = screen_json()
    doc["active"]["code"] = "main.py"
    refuse(package_zip(doc=doc, extra={"main.py": GOOD_CODE}), dest, "active.code: must be 'screen.py'")


def test_declared_code_missing(dest):
    z = zip_bytes({"screen.json": json.dumps(screen_json(code=True)).encode()})
    refuse(z, dest, "active.code: 'screen.py' not found")
