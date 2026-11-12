"""Compile and run firmware/host_test/fastpartial_host.cpp (LUT/window/timing/stats of the
fast partial refresh, fastpartial.h)."""
import os
import shutil
import subprocess

import pytest

FW = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "firmware"))


@pytest.mark.skipif(shutil.which("g++") is None, reason="g++ not available")
def test_fastpartial_host(tmp_path):
    exe = str(tmp_path / "fastpartial_host")
    subprocess.run(
        ["g++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror", f"-I{FW}/src/ee05",
         f"{FW}/host_test/fastpartial_host.cpp", "-o", exe],
        check=True,
    )
    r = subprocess.run([exe], capture_output=True, text=True, check=True)
    assert "OK" in r.stdout
