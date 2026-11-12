import sys
from pathlib import Path

PY_MODULES = Path(__file__).resolve().parents[1] / "py_modules"
sys.path.insert(0, str(PY_MODULES))

if not (PY_MODULES / "PIL" / "Image.py").is_file():
    import pytest

    pytest.exit("Pillow is not vendored (it is not in git): run decky/scripts/vendor.sh first", returncode=4)
