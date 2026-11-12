"""partitions.csv keeps two OTA app slots AND room for the idle store, no overlaps, inside 8 MB flash."""
from pathlib import Path

from inkterface_core.idle import fields as F

CSV = Path(__file__).resolve().parents[2] / "firmware" / "partitions.csv"
FLASH = 8 * 1024 * 1024
MIN_SLOT = 2 * 1024 * 1024  # the ee05 image is ~1.1 MB; refuse a table that shrinks a slot below 2 MB


def _rows():
    rows = {}
    for line in CSV.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        name, typ, sub, off, size = [c.strip() for c in line.split(",")[:5]]
        rows[name] = (typ, sub, int(off, 0), int(size, 0))
    return rows


def test_two_ota_slots_and_idle_store_fit():
    r = _rows()
    assert r["app0"][1] == "ota_0" and r["app1"][1] == "ota_1" and "otadata" in r
    assert r["app0"][3] >= MIN_SLOT and r["app1"][3] >= MIN_SLOT
    assert r["idlestore"][3] >= 2 * 65536 >= 2 * F.MAX_STORE_BYTES  # two alternating slots (docs/idle-mode.md)
    assert r["idlestore"][1] == "0x40"  # firmware finds it by custom subtype 0x40 + name


def test_no_overlap_and_inside_flash():
    spans = sorted((o, o + s, n) for n, (_, _, o, s) in _rows().items())
    for (_, end, a), (start, _, b) in zip(spans, spans[1:]):
        assert end <= start, f"{a} overlaps {b}"
    assert spans[-1][1] <= FLASH
    for _, _, off, _ in _rows().values():
        assert off % 0x1000 == 0
    r = _rows()
    assert r["app0"][2] % 0x10000 == 0 and r["app1"][2] % 0x10000 == 0  # app partitions are 64 KiB aligned
