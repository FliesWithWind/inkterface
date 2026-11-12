import pytest

from inkterface_core.sources.vdf import VdfError, parse_vdf, vget

LOGINUSERS = '''"users"
{
\t"76561198000000001"
\t{
\t\t"AccountName"\t\t"someone"
\t\t"PersonaName"\t\t"Test \\"Quoted\\" Name"
\t\t"MostRecent"\t\t"1"
\t}
}
'''


def test_nested_and_escapes():
    d = parse_vdf(LOGINUSERS)
    u = d["users"]["76561198000000001"]
    assert u["PersonaName"] == 'Test "Quoted" Name'
    assert u["MostRecent"] == "1"


def test_escape_sequences_and_windows_paths():
    d = parse_vdf(r'"k" { "a" "x\ty\nz\\w" "p" "C:\Games\Steam" "q" "trailing\\" }')
    assert d["k"]["a"] == "x\ty\nz\\w"
    assert d["k"]["p"] == r"C:\Games\Steam"  # unknown escape kept verbatim
    assert d["k"]["q"] == "trailing\\"


def test_comments_bom_and_unquoted_tokens():
    d = parse_vdf('\ufeff// header\n"AppState"\n{\n\t// inline\n\tappid 220\n\t"name" "Half-Life 2" // tail\n}\n')
    assert d == {"AppState": {"appid": "220", "name": "Half-Life 2"}}


def test_empty_and_whitespace_documents():
    assert parse_vdf("") == {} and parse_vdf("  \n\t") == {}


def test_empty_block_and_duplicate_keys_last_wins():
    d = parse_vdf('"a" { } "b" "1" "b" "2"')
    assert d == {"a": {}, "b": "2"}


def test_multiple_top_level_keys():
    assert parse_vdf('"a" "1"\n"b" { "c" "2" }') == {"a": "1", "b": {"c": "2"}}


def test_value_with_spaces_and_braces_inside_quotes():
    assert parse_vdf('"a" "x { y } z"')["a"] == "x { y } z"


@pytest.mark.parametrize("bad", [
    '"a" {',  # unclosed block
    '"a" "unterminated',  # unterminated string
    '"a"',  # key without value
    '"a" { "b" }',  # key without value inside block
    '"a" "1" }',  # stray close
    '{ "a" "1" }',  # block without key
    '"a" { { } }',  # block without key (nested)
    '"a" { "b" "1" } }',
])
def test_malformed_raises_vdferror(bad):
    with pytest.raises(VdfError):
        parse_vdf(bad)


def test_trailing_backslash_inside_unterminated_string():
    with pytest.raises(VdfError):
        parse_vdf('"a" "x\\')


def test_vget_case_insensitive_and_defaults():
    d = parse_vdf('"UserLocalConfigStore" { "software" { "Valve" { "x" "1" } } }')
    assert vget(d, "UserLocalConfigStore", "Software", "valve", "x") == "1"
    assert vget(d, "UserLocalConfigStore", "nope") is None
    assert vget(d, "UserLocalConfigStore", "nope", default=5) == 5
    assert vget(d, "UserLocalConfigStore", "software", "Valve", "x", "deeper") is None  # walks into a str
    assert vget("not a dict", "a") is None
