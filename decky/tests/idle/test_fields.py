"""Field / font tables and field_table_text()."""
from inkterface_core.idle import fields as F


def test_field_table_text_sorted_and_shaped():
    txt = F.field_table_text()
    lines = txt.splitlines()
    # one line per field, sorted by (src, id)
    assert len(lines) == len(F.FIELDS)
    parsed = [ln.split() for ln in lines]
    keys = [(int(p[0]), int(p[1])) for p in parsed]
    assert keys == sorted(keys)
    # every line has the five columns and ends with a newline
    assert txt.endswith("\n")
    for p in parsed:
        assert len(p) == 5
        assert p[3] in ("N", "S", "T", "A")


def test_field_table_text_contains_known_rows():
    txt = F.field_table_text()
    assert f"{F.SRC_CLOCK} 0 now T 0\n" in txt
    assert f"{F.SRC_WEATHER} 9 forecast.max N 3\n" in txt
    assert f"{F.SRC_HTTP_JSON} 0 value A 0\n" in txt


def test_find_field_and_path():
    fi = F.find_field(F.SRC_WEATHER, 0)
    assert fi.path == "current.temp" and fi.kind == "N"
    assert F.find_path(F.SRC_STEAM, "persona").id == 2
    assert F.find_field(F.SRC_CLOCK, 99) is None


def test_fonts_ids_and_charsets():
    assert F.font_valid(0) and F.font_valid(18)
    assert not F.font_valid(6) and not F.font_valid(7) and not F.font_valid(19)
    assert F.font_by_name("regular-28").id == 3
    assert F.font_by_name("bold-160").id == 18
    assert F.font_by_name("regular-160") is None  # no large regular
    # full fonts carry letters, digit fonts do not
    assert F.font_has_char(3, "A") and F.font_has_char(3, "\u00b0")
    assert not F.font_has_char(18, "A")
    assert F.font_has_char(18, "0") and F.font_has_char(18, ":") and F.font_has_char(18, "\u00b0")
