import hashlib
import json
import shutil
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from make_zip import make_zip  # noqa: E402


def tree(root: Path):
    for f in ("main.py", "plugin.json", "package.json", "LICENSE", "dist/index.js", "dist/index.js.map", "bin/idle_render_host",
              "py_modules/inkterface_core/a.py", "py_modules/inkterface_core/__pycache__/a.cpython-314.pyc",
              "py_modules/x.pyc", "screens/s/screen.json", "tests/test_x.py", "node_modules/y.js"):
        (root / f).parent.mkdir(parents=True, exist_ok=True)
        (root / f).write_text("x")
    (root / "bin/idle_render_host").chmod(0o755)
    (root / "vendor-requirements.txt").write_text("pillow==1 --hash=sha256:00\n")
    (root / "py_modules/PIL").mkdir()
    (root / "py_modules/PIL/Image.py").write_text("x")
    (root / "py_modules/certifi").mkdir()
    (root / "py_modules/certifi/cacert.pem").write_text("x")
    stamp(root)


def stamp(root: Path, line1=None):
    """What scripts/vendor.sh writes last: line 1 = sha256 of vendor-requirements.txt."""
    h = line1 or hashlib.sha256((root / "vendor-requirements.txt").read_bytes()).hexdigest()
    (root / "py_modules/.vendored").write_text(f"{h}\n{'0' * 64}  pillow.whl\n")


def test_zip_has_one_top_folder_and_only_what_ships(tmp_path):
    tree(tmp_path)
    n = make_zip(tmp_path, tmp_path / "out" / "p.zip")
    names = zipfile.ZipFile(tmp_path / "out" / "p.zip").namelist()
    assert n == len(names) == 10
    assert set(names) == {f"Inkterface/{x}" for x in (
        "main.py", "plugin.json", "package.json", "LICENSE", "dist/index.js", "bin/idle_render_host",
        "py_modules/inkterface_core/a.py",
        "py_modules/PIL/Image.py", "py_modules/certifi/cacert.pem", "screens/s/screen.json")}   # the .vendored stamp stays out


@pytest.mark.parametrize("missing,hint", [("dist/index.js", "pnpm"), ("bin/idle_render_host", "build_idle_render.sh"), ("main.py", ""), ("screens", ""), ("py_modules", "")])
def test_missing_piece_is_named(tmp_path, missing, hint):
    tree(tmp_path)
    p = tmp_path / missing
    shutil.rmtree(p) if p.is_dir() else p.unlink()
    with pytest.raises(FileNotFoundError, match=missing.replace(".", r"\.")) as e:
        make_zip(tmp_path, tmp_path / "p.zip")
    assert hint in str(e.value)


def test_idle_renderer_keeps_its_exec_mode_in_the_zip(tmp_path):
    """The idle preview binary is static x86_64 built by scripts/build_idle_render.sh; the zip must carry it executable."""
    tree(tmp_path)
    make_zip(tmp_path, tmp_path / "p.zip")
    info = zipfile.ZipFile(tmp_path / "p.zip").getinfo("Inkterface/bin/idle_render_host")
    assert (info.external_attr >> 16) & 0o111 == 0o111


@pytest.mark.parametrize("gone", ["PIL", "certifi"])
def test_refuses_to_build_without_the_vendored_wheels(tmp_path, gone):
    """Pillow and the CA bundle are not in git: a zip made before scripts/vendor.sh ran could not import PIL or verify TLS."""
    tree(tmp_path)
    shutil.rmtree(tmp_path / "py_modules" / gone)
    with pytest.raises(FileNotFoundError, match=r"vendor\.sh"):
        make_zip(tmp_path, tmp_path / "p.zip")
    assert not (tmp_path / "p.zip").exists()


@pytest.mark.parametrize("how", ["no_stamp", "stale_stamp", "no_requirements"])
def test_refuses_a_half_or_stale_vendor_step(tmp_path, how):
    tree(tmp_path)
    if how == "no_stamp":   # interrupted vendor.sh: files there, stamp (written last) not
        (tmp_path / "py_modules/.vendored").unlink()
    elif how == "stale_stamp":   # requirements bumped after the last vendor run
        (tmp_path / "vendor-requirements.txt").write_text("pillow==2 --hash=sha256:11\n")
    else:
        (tmp_path / "vendor-requirements.txt").unlink()
    with pytest.raises(FileNotFoundError, match=r"vendor\.sh"):
        make_zip(tmp_path, tmp_path / "p.zip")


def test_release_zip_plugin_json_has_no_debug_flag(tmp_path):
    """Decky 3.3.1 (loader.py import_plugin): "debug" makes the loader hot-reload the plugin on file changes. Dev-only;
    the Decky store does not accept it either. Developers set it on their installed copy (docs/RELEASING.md)."""
    tree(tmp_path)
    shutil.copy(Path(__file__).resolve().parents[1] / "plugin.json", tmp_path / "plugin.json")   # the real file
    make_zip(tmp_path, tmp_path / "p.zip")
    j = json.loads(zipfile.ZipFile(tmp_path / "p.zip").read("Inkterface/plugin.json"))
    assert "debug" not in j["flags"] and "root" not in j["flags"]
    # store image: this repo's own emulator-rendered screen, not the template's SteamDeckHomebrew placeholder
    img = j["publish"]["image"]
    prefix = "https://raw.githubusercontent.com/FliesWithWind/inkterface/main/"
    assert img.startswith(prefix) and "steamdeckhomebrew" not in img.lower()
    assert (Path(__file__).resolve().parents[2] / img[len(prefix):]).is_file()   # the file the URL points at is in the repo


def test_images_the_bundle_imports_ship_in_dist_assets(tmp_path):
    """The quick-access icon is an imported PNG: rollup writes its URL into index.js and Decky serves it from dist/assets."""
    tree(tmp_path)
    (tmp_path / "dist/assets").mkdir()
    (tmp_path / "dist/assets/Icon-ab12.png").write_bytes(b"png")
    (tmp_path / "dist/index.js").write_text('const i="http://127.0.0.1:1337/plugins/Inkterface/assets/Icon-ab12.png";')
    make_zip(tmp_path, tmp_path / "p.zip")
    assert "Inkterface/dist/assets/Icon-ab12.png" in zipfile.ZipFile(tmp_path / "p.zip").namelist()


def test_refuses_a_bundle_whose_image_is_not_in_dist_assets(tmp_path):
    tree(tmp_path)
    (tmp_path / "dist/index.js").write_text('"http://127.0.0.1:1337/plugins/Inkterface/assets/Gone-ff.png"')
    with pytest.raises(FileNotFoundError, match=r"Gone-ff\.png"):
        make_zip(tmp_path, tmp_path / "p.zip")
    assert not (tmp_path / "p.zip").exists()


def test_the_real_quick_access_icon_is_the_inkterface_icon():
    """src/index.tsx imports assets/valve/icon.png (checked in vitest too); the file is where the import says."""
    src = (Path(__file__).resolve().parents[1] / "src/index.tsx").read_text()
    assert '"../../assets/valve/icon.png"' in src
    assert (Path(__file__).resolve().parents[2] / "assets/valve/icon.png").is_file()
