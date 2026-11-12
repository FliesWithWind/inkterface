"""Helpers: build package zips (good and hostile) in memory."""
import io
import json
import stat
import zipfile
from pathlib import Path

DECKY = Path(__file__).resolve().parents[2]
BUILTIN_DIR = DECKY / "screens"
EXAMPLES = DECKY / "examples"

GOOD_CODE = '''
import io
from PIL import Image
def render(canvas_size, data, settings):
    img = Image.new("1", canvas_size, 1)
    for x in range(40):
        img.putpixel((x, 5), 0)
    b = io.BytesIO()
    img.save(b, "PNG")
    return b.getvalue()
'''


def screen_json(pid="demo-pkg", version="1.0.0", code=False, **over):
    doc = {"schema": 1, "id": pid, "name": f"Demo {pid}", "version": version, "author": "Tester",
           "active": {"widgets": [{"type": "text", "x": 0, "y": 0, "w": 200, "h": 30, "text": f"v{version}"}]}}
    if code:
        doc["active"]["code"] = "screen.py"
    doc.update(over)
    return doc


def package_files(pid="demo-pkg", version="1.0.0", code=None, extra=None, doc=None):
    """name-inside-folder -> bytes. `code` = screen.py source (makes it a code-tier package)."""
    files = {"screen.json": json.dumps(doc or screen_json(pid, version, code=code is not None)).encode()}
    if code is not None:
        files["screen.py"] = code.encode()
    for k, v in (extra or {}).items():
        files[k] = v if isinstance(v, bytes) else v.encode()
    return files


def zip_bytes(entries, top="demo-pkg", compress=zipfile.ZIP_DEFLATED):
    """entries: name -> bytes. Names are prefixed with `top/` unless top is None."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compress) as z:
        for name, data in entries.items():
            z.writestr(f"{top}/{name}" if top else name, data)
    return buf.getvalue()


def package_zip(pid="demo-pkg", version="1.0.0", code=None, extra=None, doc=None):
    return zip_bytes(package_files(pid, version, code, extra, doc), top=pid)


def raw_zip(infos):
    """infos: list of (ZipInfo, bytes). Written as-is, no name rewriting."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for zi, data in infos:
            z.writestr(zi, data)
    return buf.getvalue()


def zinfo(name, mode=None, kind=stat.S_IFREG):
    zi = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
    zi.create_system = 3
    zi.compress_type = zipfile.ZIP_DEFLATED
    zi.external_attr = ((kind | (mode if mode is not None else 0o644)) << 16)
    return zi


def with_json(extra_infos, pid="demo-pkg"):
    """A valid package plus hostile extra entries."""
    base = [(zinfo(f"{pid}/screen.json"), package_files(pid)["screen.json"])]
    return raw_zip(base + extra_infos)
