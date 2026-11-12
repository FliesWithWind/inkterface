"""docs/plugin-ui.md must mention every callable and event the backend and frontend really have."""
import ast
import re
from pathlib import Path

DECKY = Path(__file__).resolve().parents[2]
DOC = (DECKY.parent / "docs" / "plugin-ui.md").read_text()


def test_every_callable_is_documented():
    tree = ast.parse((DECKY / "main.py").read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Plugin")
    public = [n.name for n in cls.body if isinstance(n, ast.AsyncFunctionDef) and not n.name.startswith("_")]
    assert len(public) == 31   # + catalog_*; + search_places, locate_by_ip, resolve_steam_id, steam_profile, test_steam_key, wifi_current
    for name in public:
        assert f"`{name}`" in DOC, name


def test_every_event_is_documented():
    src = (DECKY / "main.py").read_text()
    events = set(re.findall(r'"(\w+_changed)"', src)) | {"status"} | set(re.findall(r'decky\.emit\("(firmware)"', src))
    assert events == {"status", "settings_changed", "screens_changed", "firmware"}
    for e in events:
        assert f"`{e}`" in DOC


def test_frontend_wraps_exactly_the_backend_callables():
    backend = {n for n in re.findall(r"async def (\w+)\(", (DECKY / "main.py").read_text()) if not n.startswith("_")}
    used = set(re.findall(r'\("([a-z_]+)"\)', (DECKY / "src" / "api.ts").read_text()))
    assert used == backend, used ^ backend
