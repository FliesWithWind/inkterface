import json
import os
from pathlib import Path

import pytest

DECKY = Path(__file__).resolve().parents[2]
BUILTIN_DIR = DECKY / "screens"
GOLDEN_DIR = Path(__file__).resolve().parent / "goldens"


def pytest_addoption(parser):
    # Registered only when this directory is a pytest argument:
    #   pytest decky/tests/screens --update-goldens
    # From a wider run use INKTERFACE_UPDATE_GOLDENS=1 instead.
    parser.addoption("--update-goldens", action="store_true", help="rewrite golden PNGs from current output")


@pytest.fixture
def update_goldens(request):
    return request.config.getoption("--update-goldens", default=False) or os.environ.get("INKTERFACE_UPDATE_GOLDENS") == "1"


@pytest.fixture
def make_pkg(tmp_path):
    """make_pkg(data_dict, assets={'name.png': bytes}, files={'screen.py': text}, name=None) -> package path."""

    def _make(data, assets=None, files=None, name=None, raw=None):
        root = tmp_path / (name or (data.get("id") if isinstance(data, dict) and data.get("id") else "pkg"))
        root.mkdir(exist_ok=True)
        (root / "screen.json").write_text(raw if raw is not None else json.dumps(data))
        for fname, content in (assets or {}).items():
            p = root / "assets" / fname
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(content)
        for fname, content in (files or {}).items():
            (root / fname).write_text(content)
        return root

    return _make
