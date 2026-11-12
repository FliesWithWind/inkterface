import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from packages_testlib import BUILTIN_DIR  # noqa: E402

from inkterface_core.packages import PackageStore  # noqa: E402


@pytest.fixture
def store(tmp_path):
    return PackageStore(tmp_path / "store", BUILTIN_DIR)


@pytest.fixture
def write_zip(tmp_path):
    def _w(data, name="p.zip"):
        p = tmp_path / name
        p.write_bytes(data)
        return p
    return _w
