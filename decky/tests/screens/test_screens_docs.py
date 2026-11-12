"""docs/screen-format.md must stay in step with the code, and its example must work."""
import json
import re
from pathlib import Path

import pytest

from inkterface_core.screens import data as D
from inkterface_core.screens import load_screen, render
from inkterface_core.screens.emulate import main
from inkterface_core.screens.widgets import COMMON, WIDGETS

DOC = Path(__file__).resolve().parents[3] / "docs" / "screen-format.md"
TEXT = DOC.read_text()


def section(widget_type):
    m = re.search(rf"#### `{re.escape(widget_type)}`\n(.*?)(?=\n#### |\n## )", TEXT, re.S)
    assert m, f"no '#### `{widget_type}`' section in the doc"
    return m.group(1)


@pytest.mark.parametrize("wtype", sorted(WIDGETS))
def test_every_widget_option_is_documented(wtype):
    body = section(wtype)
    for opt, spec in WIDGETS[wtype].options.items():
        assert f"| `{opt}` |" in body, f"{wtype}.{opt} missing in the doc"
        if spec.default is not None and spec.kind == "enum":
            assert f"`{spec.default}`" in body
    if WIDGETS[wtype].bind_kinds:
        assert "`bind`" in body


def test_common_fields_documented():
    for k in COMMON:
        assert f"| `{k}` |" in TEXT


def test_every_source_is_documented():
    for path in D.SOURCES:
        if path.endswith(".history"):
            continue
        assert path.split(".")[0] in TEXT and path.rsplit(".", 1)[-1] in TEXT, path
    assert "`<each system path above>.history`" in TEXT


def test_every_icon_name_documented():
    from inkterface_core.screens.icons import ICONS
    for name in ICONS:
        assert f"`{name}`" in TEXT


def test_font_licence_and_idle_note_present():
    assert "SIL Open Font License" in TEXT and "OFL.txt" in TEXT
    assert "rendered by firmware" in TEXT
    assert (Path(__file__).resolve().parents[2] / "py_modules/inkterface_core/screens/fonts/OFL.txt").is_file()


def test_full_example_validates_and_renders(tmp_path):
    blocks = re.findall(r"```json\n(.*?)```", TEXT, re.S)
    screen_json = next(b for b in blocks if '"my-screen"' in b)
    sample = next(b for b in blocks if '"system.fan.rpm": 2800' in b)
    pkg = tmp_path / "my-screen"
    pkg.mkdir()
    (pkg / "screen.json").write_text(screen_json)
    (pkg / "sample-data.json").write_text(sample)
    s = load_screen(pkg)
    img = render(s, json.loads(sample))
    assert img.size == (648, 480)
    assert main([str(pkg), "--out", str(tmp_path / "o.png")]) == 0


def test_builtin_descriptions_do_not_name_a_render_mode():
    """The Idle page shows the render mode as a badge and a switch; a description saying 'mono' or '4-level gray' would contradict it."""
    import json
    import re

    from conftest import BUILTIN_DIR

    words = re.compile(r"\b(mono|monochrome|gray\w*|grey\w*|4-level|4-gray|black and white)\b", re.I)
    for f in sorted(BUILTIN_DIR.glob("*/screen.json")):
        desc = json.loads(f.read_text())["description"]
        assert not words.search(desc), f"{f.parent.name}: {desc!r}"
