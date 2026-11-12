"""Compile firmware/host_test/idle_sources_host.cpp and run it against a real
python http.server fake (fake_http.py) on 127.0.0.1.

Skips if g++ or the vendored ArduinoJson headers are missing (they live under
firmware/.pio/libdeps/ee05/ArduinoJson/src, populated by `pio run`)."""
import os
import shutil
import socket
import subprocess
import time

import pytest

HERE = os.path.dirname(__file__)
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))  # repo root
FW = os.path.join(ROOT, "firmware")
AJ = os.path.join(FW, ".pio", "libdeps", "ee05", "ArduinoJson", "src")
FAKE = os.path.join(HERE, "fake_http.py")


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_up(port, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection(("127.0.0.1", port), 0.2):
                return True
        except OSError:
            time.sleep(0.05)
    return False


@pytest.mark.skipif(shutil.which("g++") is None, reason="g++ not available")
@pytest.mark.skipif(not os.path.isdir(AJ), reason="ArduinoJson headers not vendored")
def test_idle_sources_host(tmp_path):
    exe = str(tmp_path / "idle_sources_host")
    subprocess.run(
        ["g++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
         f"-I{FW}/src/ee05", f"-I{AJ}",
         f"{FW}/host_test/idle_sources_host.cpp",
         f"{FW}/src/ee05/idle_sources.cpp", "-o", exe],
        check=True,
    )

    port = _free_port()
    reqlog = str(tmp_path / "requests.log")
    env = dict(os.environ, IDLE_REQ_LOG=reqlog)
    server = subprocess.Popen(["python3", FAKE, str(port), reqlog])
    try:
        assert _wait_up(port), "fake server did not come up"
        base = f"http://127.0.0.1:{port}"
        # port 1 refuses connections for the HTTP_NET case
        r = subprocess.run([exe, base, "127.0.0.1:1"], env=env,
                           capture_output=True, text=True)
        print(r.stdout)
        print(r.stderr)
        assert r.returncode == 0, f"host test failed:\n{r.stdout}\n{r.stderr}"
        assert "idle sources host OK" in r.stdout
    finally:
        server.terminate()
        server.wait(timeout=5)
