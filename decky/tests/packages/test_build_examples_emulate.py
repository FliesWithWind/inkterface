"""make_zip / make_package.py, the example packages, and `emulate --package`."""
import hashlib
import importlib.util
import os
import zipfile

import pytest
from packages_testlib import BUILTIN_DIR, EXAMPLES, GOOD_CODE, package_files
from PIL import Image

from inkterface_core.packages import PackageError, PackageStore, make_zip
from inkterface_core.screens import ScreenError
from inkterface_core.screens.emulate import emulate, emulate_zip, main

EXAMPLE_DIRS = sorted(p for p in EXAMPLES.iterdir() if (p / "screen.json").is_file())


def folder(tmp_path, pid="demo-pkg", **kw):
    d = tmp_path / pid
    for name, data in package_files(pid=pid, **kw).items():
        (d / name).parent.mkdir(parents=True, exist_ok=True)
        (d / name).write_bytes(data)
    return d


# ---- make_zip -----------------------------------------------------------------------------------------------
def test_make_zip_layout_and_default_name(tmp_path):
    out = make_zip(folder(tmp_path, extra={"assets/a.txt": "x"}))
    assert out == tmp_path / "demo-pkg-1.0.0.zip"
    with zipfile.ZipFile(out) as z:
        assert sorted(z.namelist()) == ["demo-pkg/assets/a.txt", "demo-pkg/screen.json"]
        assert all(i.date_time == (1980, 1, 1, 0, 0, 0) and i.external_attr >> 16 == 0o100644 for i in z.infolist())


def test_make_zip_is_reproducible(tmp_path):
    d = folder(tmp_path)
    a = make_zip(d, tmp_path / "a.zip").read_bytes()
    os.utime(d / "screen.json", (1, 1))
    assert make_zip(d, tmp_path / "b.zip").read_bytes() == a


def test_make_zip_skips_junk(tmp_path):
    d = folder(tmp_path, extra={".DS_Store": "x", "__pycache__/a.pyc": "x", "old.zip": "x", "x.pyc": "x",
                                ".git/config": "x", "assets/.hidden": "x"})
    with zipfile.ZipFile(make_zip(d, tmp_path / "o.zip")) as z:
        assert z.namelist() == ["demo-pkg/screen.json"]


def test_make_zip_output_inside_folder_is_not_included(tmp_path):
    d = folder(tmp_path)
    make_zip(d, d / "out.zip")
    with zipfile.ZipFile(make_zip(d, d / "out.zip")) as z:
        assert z.namelist() == ["demo-pkg/screen.json"]


def test_make_zip_refuses_invalid_folder(tmp_path):
    d = folder(tmp_path)
    (d / "screen.json").write_text("{")
    with pytest.raises(ScreenError, match="invalid JSON"):
        make_zip(d)


def test_make_zip_refuses_executables(tmp_path):
    with pytest.raises(PackageError, match="helper.py: executable file types"):
        make_zip(folder(tmp_path, code=GOOD_CODE, extra={"helper.py": "x"}))


def test_make_zip_refuses_symlinked_file(tmp_path):
    d = folder(tmp_path)
    (tmp_path / "secret.txt").write_text("s")
    (d / "link.txt").symlink_to(tmp_path / "secret.txt")
    with pytest.raises(PackageError, match="symlinks are not allowed"):
        make_zip(d, tmp_path / "o.zip")
    assert not (tmp_path / "o.zip").exists()


def test_make_zip_refuses_symlinked_dir(tmp_path):
    d = folder(tmp_path)
    (tmp_path / "other").mkdir()
    (d / "dirlink").symlink_to(tmp_path / "other", target_is_directory=True)
    with pytest.raises(PackageError, match="symlinks are not allowed"):
        make_zip(d, tmp_path / "o.zip")


def test_make_zip_removes_output_when_reimport_fails(tmp_path):
    d = folder(tmp_path, extra={"big.bin": os.urandom(5 * 1024 * 1024 + 1)})
    with pytest.raises(PackageError, match="zip is .* bytes"):
        make_zip(d, tmp_path / "o.zip")
    assert not (tmp_path / "o.zip").exists()


# ---- make_package.py CLI -------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def cli():
    spec = importlib.util.spec_from_file_location("make_package", EXAMPLES / "make_package.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_make_package_cli(cli, tmp_path, capsys):
    assert cli.main([str(folder(tmp_path)), "-o", str(tmp_path / "x.zip")]) == 0
    assert capsys.readouterr().out.strip() == str(tmp_path / "x.zip") and (tmp_path / "x.zip").is_file()


def test_make_package_cli_reports_errors(cli, tmp_path, capsys):
    d = folder(tmp_path)
    (d / "screen.json").write_text('{"schema": 1}')
    assert cli.main([str(d)]) == 1
    assert "screen.json: id: required" in capsys.readouterr().err


# ---- example packages: build -> install -> (approve) -> render ----------------------------------------------
@pytest.mark.parametrize("ex", EXAMPLE_DIRS, ids=lambda p: p.name)
def test_example_end_to_end(ex, tmp_path):
    zp = make_zip(ex, tmp_path / f"{ex.name}.zip")
    store = PackageStore(tmp_path / "s", BUILTIN_DIR)
    info = store.install(zp)
    assert info.id == ex.name and info.needs_approval == info.has_code
    if info.has_code:
        assert (ex / "screen.py").is_file()
        store.approve(info.id, info.version, hashlib.sha256((ex / "screen.py").read_bytes()).hexdigest())
    im = store.render(info.id, {"system.cpu.load": 42})
    assert im.size == (648, 480) and im.convert("L").tobytes().count(0) > 500


def test_both_example_kinds_exist():
    assert {p.name for p in EXAMPLE_DIRS} >= {"hello-screen", "hello-code-screen"}


# ---- emulate --package ---------------------------------------------------------------------------------------
def zip_example(name, tmp_path):
    return make_zip(EXAMPLES / name, tmp_path / f"{name}.zip")


def test_emulate_package_declarative(tmp_path, capsys):
    out = tmp_path / "o.png"
    assert main(["--package", str(zip_example("hello-screen", tmp_path)), "--out", str(out)]) == 0
    assert Image.open(out).size == (648, 480) and "ok" in capsys.readouterr().out


def test_emulate_package_code_needs_allow_code(tmp_path, capsys):
    zp, out = zip_example("hello-code-screen", tmp_path), tmp_path / "o.png"
    assert main(["--package", str(zp), "--out", str(out)]) == 1
    assert "--allow-code" in capsys.readouterr().err and not out.exists()
    assert main(["--package", str(zp), "--out", str(out), "--allow-code"]) == 0
    assert Image.open(out).mode == "1"


def test_emulate_package_validates_before_rendering(tmp_path, capsys):
    from packages_testlib import with_json, zinfo
    bad = tmp_path / "bad.zip"
    bad.write_bytes(with_json([(zinfo("demo-pkg/../evil"), b"x")]))
    assert main(["--package", str(bad), "--out", str(tmp_path / "o.png")]) == 1
    assert "path traversal" in capsys.readouterr().err and not (tmp_path / "o.png").exists()


def test_emulate_package_default_out_name(tmp_path, monkeypatch):
    zp = zip_example("hello-screen", tmp_path)
    monkeypatch.chdir(tmp_path)
    screen, out = emulate_zip(zp, None)
    assert screen.id == "hello-screen" and out.name == "hello-screen.png" and out.is_file()


def test_emulate_package_set_and_data(tmp_path):
    zp = zip_example("hello-screen", tmp_path)
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    emulate_zip(zp, a)
    emulate_zip(zp, b, settings={"greeting": "Moin"})
    assert a.read_bytes() != b.read_bytes()


def test_emulate_package_non_object_data(tmp_path):
    data = tmp_path / "d.json"
    data.write_text("[]")
    with pytest.raises(ScreenError, match="top level must be an object"):
        emulate_zip(zip_example("hello-screen", tmp_path), tmp_path / "o.png", data)


def test_emulate_usage_mentions_zip(capsys):
    for argv in ([], ["--package", "a.zip", "--all", "b"], ["x", "--package", "a.zip"]):
        with pytest.raises(SystemExit) as e:
            main(argv)
        assert e.value.code == 2
    assert "--package ZIP" in capsys.readouterr().err


def test_emulate_folder_with_code_needs_allow_code(tmp_path):
    ex = EXAMPLES / "hello-code-screen"
    with pytest.raises(ScreenError, match="pass --allow-code"):
        emulate(ex, tmp_path / "o.png")
    emulate(ex, tmp_path / "o.png", allow_code=True)
    assert Image.open(tmp_path / "o.png").size == (648, 480)


def test_emulate_all_with_code(tmp_path, capsys):
    assert main(["--all", str(EXAMPLES), "--out", str(tmp_path / "o")]) == 1
    cap = capsys.readouterr()
    assert "ok    hello-screen" in cap.out and "FAIL  hello-code-screen" in cap.err
    assert main(["--all", str(EXAMPLES), "--out", str(tmp_path / "o"), "--allow-code"]) == 0


def test_emulate_code_failure_exit_1(tmp_path, capsys):
    d = folder(tmp_path, code="def render(c, d, s):\n    raise ValueError('nope')\n")
    assert main([str(d), "--allow-code", "--out", str(tmp_path / "o.png")]) == 1
    assert "screen.py: ValueError: nope" in capsys.readouterr().err
