import pytest
from PIL import Image

from inkterface_core import HEIGHT, WIDTH, render_test_frame

STRIDE = WIDTH // 8  # 81 bytes per row, rows are byte-aligned (648 = 81 * 8)


@pytest.fixture(scope="module")
def frame():
    return render_test_frame()


def black(frame, x, y):
    """Pixel (x, y) is black. Packing: row-major, MSB first, bit 1 = white."""
    return not (frame[y * STRIDE + x // 8] >> (7 - x % 8)) & 1


def test_size_and_type(frame):
    assert (WIDTH, HEIGHT) == (648, 480)
    assert isinstance(frame, bytes)
    assert len(frame) == 38880 == STRIDE * HEIGHT


def test_deterministic(frame):
    assert render_test_frame() == frame


def test_background_white_and_has_ink(frame):
    assert black(frame, 0, 0) is False and black(frame, WIDTH - 1, HEIGHT - 1) is False
    assert frame[-1] == 0xFF
    assert any(b != 0xFF for b in frame)


def test_rectangle_edges_and_interior(frame):
    # outline rect (60,240)-(588,420), width 4; checks also pin the bit order (x=60 -> bit 3, x=59 -> bit 4)
    assert black(frame, 60, 300) and black(frame, 63, 300)
    assert not black(frame, 59, 300) and not black(frame, 64, 300)
    assert black(frame, 300, 240) and black(frame, 300, 419)
    assert not black(frame, 300, 350)  # interior stays white


def test_line_and_text_present(frame):
    assert black(frame, 20, 100)                      # line start
    assert any(black(frame, x, y) for x in range(20, 250) for y in range(20, 80))  # title text


def test_matches_pillow_roundtrip(frame):
    img = Image.frombytes("1", (WIDTH, HEIGHT), frame)
    assert img.size == (WIDTH, HEIGHT) and img.getpixel((0, 0)) == 255 and img.getpixel((61, 300)) == 0
    assert img.tobytes() == frame
