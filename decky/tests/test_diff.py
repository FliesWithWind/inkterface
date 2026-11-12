import random

import pytest

from inkterface_core import diff as D
from inkterface_core import proto as P


def covered(rects, x, y):
    return any(rx <= x < rx + rw and ry <= y < ry + rh for rx, ry, rw, rh in rects)


def test_identical_frames_no_rects():
    f = bytes(P.FB_BYTES)
    assert D.diff_rects(f, f) == []


def test_single_pixel_is_one_8x1_aligned_rect():
    new = bytearray(P.FB_BYTES)
    new[5 * P.STRIDE + 3] = 0x10  # pixel x=27,y=5
    assert D.diff_rects(bytes(P.FB_BYTES), bytes(new)) == [(24, 5, 8, 1)]


def test_far_apart_changes_stay_separate():
    new = bytearray(P.FB_BYTES)
    new[0] = 1
    new[P.FB_BYTES - 1] = 1
    r = D.diff_rects(bytes(P.FB_BYTES), bytes(new))
    assert r == [(0, 0, 8, 1), (640, 479, 8, 1)]


def test_adjacent_rows_merge():
    new = bytearray(P.FB_BYTES)
    for y in range(10, 20):
        new[y * P.STRIDE + 2] = 0xFF
    assert D.diff_rects(bytes(P.FB_BYTES), bytes(new)) == [(16, 10, 8, 10)]


def test_full_frame_change_splits_to_payload_limit():
    new = bytes([0xFF]) * P.FB_BYTES
    rects = D.diff_rects(bytes(P.FB_BYTES), new)
    assert sum(h for *_, h in rects) == 480 and all(x == 0 and w == 648 for x, _, w, _ in rects)
    assert all(8 + h * (w // 8) <= P.MAX_PAYLOAD for _, _, w, h in rects)
    assert len(rects) == 10  # 9 x 50 rows + 30


def test_rects_aligned_in_bounds_and_cover_every_changed_pixel():
    rnd = random.Random(1)
    prev = bytes(rnd.getrandbits(8) for _ in range(P.FB_BYTES))
    b = bytearray(prev)
    for _ in range(300):
        b[rnd.randrange(P.FB_BYTES)] ^= 1 << rnd.randrange(8)
    new = bytes(b)
    rects = D.diff_rects(prev, new)
    for x, y, w, h in rects:
        assert x % 8 == 0 and w % 8 == 0 and w > 0 and h > 0 and x + w <= 648 and y + h <= 480
        P.rect_payload(x, y, w, h, D.crop(new, (x, y, w, h)))  # fits a frame
    for y in range(480):
        for bx in range(81):
            if prev[y * 81 + bx] != new[y * 81 + bx]:
                assert covered(rects, bx * 8, y)


def test_wrong_size_rejected():
    with pytest.raises(ValueError):
        D.diff_rects(b"", b"")


def test_helpers_blank_pixel_crop():
    fb = bytearray(D.blank())
    assert len(fb) == P.FB_BYTES and not any(fb)
    fb[3 * P.STRIDE + 2] = 0b10000001  # x=16 and x=23, y=3
    fb = bytes(fb)
    assert [D.pixel(fb, x, 3) for x in (15, 16, 17, 22, 23, 24)] == [0, 1, 0, 0, 1, 0]
    assert D.crop(fb, (16, 3, 8, 1)) == b"\x81"
    assert D.crop(fb, (8, 2, 16, 2)) == bytes([0, 0, 0, 0x81])


def test_split_rect_narrow_tall_and_exact_fit():
    assert D.split_rect((0, 0, 8, 480)) == [(0, 0, 8, 480)]  # 480 rows * 1 B + 8 < 4096
    bands = D.split_rect((0, 7, 648, 100))
    assert bands == [(0, 7, 648, 50), (0, 57, 648, 50)]
    assert D.split_rect((0, 0, 648, 1)) == [(0, 0, 648, 1)]


def test_from_image_roundtrip_and_size_check():
    from PIL import Image

    im = Image.new("1", (P.WIDTH, P.HEIGHT), 1)  # all white
    im.putpixel((0, 0), 0)  # one black pixel
    fb = D.from_image(im)
    assert D.pixel(fb, 0, 0) == 1 and D.pixel(fb, 1, 0) == 0 and sum(bin(b).count("1") for b in fb) == 1
    with pytest.raises(ValueError):
        D.from_image(Image.new("1", (10, 10)))


def test_merge_heuristic_keeps_distant_rows_apart_but_close_rows_together():
    new = bytearray(P.FB_BYTES)
    new[10 * P.STRIDE] = 1
    new[12 * P.STRIDE] = 1  # 1 gap row of 1 byte: cheaper to merge than a second 18 B RECT
    assert D.diff_rects(bytes(P.FB_BYTES), bytes(new)) == [(0, 10, 8, 3)]
    new[300 * P.STRIDE + 80] = 1
    assert len(D.diff_rects(bytes(P.FB_BYTES), bytes(new))) == 2
