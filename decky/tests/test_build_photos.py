"""The build photos in the readme: present, small enough, no metadata that identifies a person or place, and described with alt text."""
import re
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
PHOTOS = ["live-telemetry.webp", "steam-machine-front.jpg", "build-back.jpg"]
LIMIT = 2 * 1024 * 1024  # the repository's cap for one tracked file (tools/check_file_sizes.py)


def test_photos_are_small_and_carry_no_exif_or_gps():
    for name in PHOTOS:
        path = ROOT / "docs" / "img" / "build" / name
        assert 0 < path.stat().st_size < LIMIT, name
        with Image.open(path) as im:
            assert dict(im.getexif()) == {}, name  # no camera, date or GPS tags
            assert "exif" not in im.info and "xmp" not in im.info and "XML:com.adobe.xmp" not in im.info, name
    with Image.open(ROOT / "docs" / "img" / "build" / "live-telemetry.webp") as im:
        assert getattr(im, "n_frames", 1) > 1  # the animated hero


def test_readme_and_hardware_doc_use_every_photo_with_alt_text():
    readme = (ROOT / "readme.md").read_text(encoding="utf-8")
    hardware = (ROOT / "docs" / "hardware.md").read_text(encoding="utf-8")
    for name in PHOTOS:
        assert readme.count(f"docs/img/build/{name}") == 1, name
    assert hardware.count("img/build/build-back.jpg") == 1
    for text, path_pat in ((readme, r"docs/img/build/"), (hardware, r"img/build/")):
        for alt, _ in re.findall(r"!\[([^\]]*)\]\(" + path_pat + r"([^)]+)\)", text):
            assert len(alt) > 15, alt
    assert re.search(r'<img src="docs/img/build/live-telemetry.webp" alt="[^"]{15,}"', readme)
    assert readme.index("live-telemetry.webp") < readme.index("## Screenshots") < readme.index("## The build") < readme.index("## Features")
