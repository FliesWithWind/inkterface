"""Example code-tier screen. Runs in a guarded child process, so keep it quick (2 s) and self-contained.

render(canvas_size, data, settings) must return PNG bytes (or a PIL image) that is exactly canvas_size and 1-bit.
`data` is the host's data snapshot as a plain dict; `settings` are the resolved screen settings.
"""
import io

from PIL import Image, ImageDraw, ImageFont


def render(canvas_size, data, settings):
    img = Image.new("1", canvas_size, 1)          # 1 = white
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, canvas_size[0] - 1, canvas_size[1] - 1), outline=0, width=6)
    big, small = ImageFont.load_default(size=40), ImageFont.load_default(size=28)
    d.text((32, 32), str(settings["title"]), font=big, fill=0)
    load = data.get("system.cpu.load")
    d.text((32, 100), "CPU load: " + ("--" if load is None else f"{load:.0f}%"), font=small, fill=0)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()
