from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 648, 480


def render_test_frame() -> bytes:
    """648x480 1-bit test image, packed MSB-first, row-major, 1=white. 38880 bytes."""
    img = Image.new("1", (WIDTH, HEIGHT), 1)
    d = ImageDraw.Draw(img)
    d.text((20, 20), "Inkterface", fill=0, font=ImageFont.load_default(size=48))
    d.line((20, 100, WIDTH - 20, 200), fill=0, width=3)
    d.rectangle((60, 240, WIDTH - 60, HEIGHT - 60), outline=0, width=4)
    return img.tobytes()
