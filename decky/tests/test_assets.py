"""assets/valve: Valve's artwork is byte-identical to their repo (pinned by sha256), the README names every file, and
the icon pair is what the README says it is (256 px = nearest-neighbour enlargement of the 48 px grid)."""
import hashlib
from pathlib import Path

from PIL import Image

DIR = Path(__file__).resolve().parents[2] / "assets" / "valve"
# sha256 of the files at Valve's inkterface commit 477563c (git show 477563c:<path> | sha256sum)
SHA = {
    "icon.png": "43b497467246181e05d99465b7e8e9f8b3a29dc9c620f87566a14b1ac045d3f5",
    "icon256.png": "57df5512d2bd1d96c1fd63f84ab75dc10d40564491b933c1b08e07e9da5f2fc1",
    "Library_Logo.png": "15aa00e11e5c1fc5b6811a0fe97fc624962e656c118299658ab781b60a4dbad3",
}


def test_files_are_valves_byte_for_byte():
    for f, want in SHA.items():
        assert hashlib.sha256((DIR / f).read_bytes()).hexdigest() == want, f


def test_every_file_is_documented_and_only_those_live_there():
    readme = (DIR / "README.md").read_text(encoding="utf-8")
    assert "477563c" in readme and "gitlab.steamos.cloud/SteamHardware/SteamMachine/inkterface" in readme
    assert sorted(p.name for p in DIR.iterdir()) == sorted([*SHA, "README.md"])
    for f in SHA:
        assert f in readme, f


def test_icon_is_a_48px_grid_and_the_256_is_its_nearest_neighbour_enlargement():
    small, big = Image.open(DIR / "icon.png").convert("RGBA"), Image.open(DIR / "icon256.png").convert("RGBA")
    assert small.size == (48, 48) and big.size == (256, 256)
    assert big.resize((48, 48), Image.NEAREST).tobytes() == small.tobytes()
    assert len(small.getcolors()) <= 8   # a few flat colours: pixel art, not a smoothed picture


def test_the_wordmark_loads():
    Image.open(DIR / "Library_Logo.png").verify()
