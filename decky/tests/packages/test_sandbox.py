"""run_code / _runner: real child processes. Every misbehaving screen.py must raise PackageError, never hurt the host."""
import base64
import io
import json
import os
import sys
import textwrap
import time

import pytest
from packages_testlib import GOOD_CODE
from PIL import Image

from inkterface_core.packages import PackageError, error_frame, run_code
from inkterface_core.packages import _runner as R
from inkterface_core.packages import sandbox as S


def run(code, tmp_path, data=None, settings=None, **kw):
    kw.setdefault("timeout", 5)
    return run_code(textwrap.dedent(code).encode(), tmp_path, data if data is not None else {},
                    settings if settings is not None else {}, **kw)


def fails(code, tmp_path, match, **kw):
    with pytest.raises(PackageError, match=match):
        run(code, tmp_path, **kw)


def png(size=(648, 480), mode="1", fmt="PNG"):
    b = io.BytesIO()
    Image.new(mode, size, 1 if mode == "1" else 255).save(b, fmt)
    return b.getvalue()


def returning(blob):
    return f"def render(c, d, s):\n    return {blob!r}\n"


# ---- success ------------------------------------------------------------------------------------------------
def test_success_bytes(tmp_path):
    im = run(GOOD_CODE, tmp_path)
    assert im.size == (648, 480) and im.mode == "1"
    assert im.getpixel((0, 5)) == 0 and im.getpixel((100, 100)) == 255


def test_success_pil_image_return(tmp_path):
    im = run("""
        from PIL import Image
        def render(c, d, s):
            return Image.new("1", c, 0)
        """, tmp_path)
    assert im.getpixel((10, 10)) == 0


def test_receives_size_data_settings(tmp_path):
    fails("""
        def render(c, d, s):
            raise RuntimeError(repr((c, d, s)))
        """, tmp_path, r"\(\(648, 480\), \{'a': \[1, 2\]\}, \{'t': 'x'\}\)", data={"a": [1, 2]}, settings={"t": "x"})


def test_datetimes_in_the_live_snapshot_arrive_as_iso_strings(tmp_path):
    """The registry snapshot holds an aware datetime (time.now); it used to make every code screen an error frame."""
    import datetime
    now = datetime.datetime(2026, 11, 10, 3, 5, tzinfo=datetime.timezone.utc)
    fails("""
        def render(c, d, s):
            raise RuntimeError(repr(d))
        """, tmp_path, r"2026-11-10T03:05:00\+00:00", data={"time.now": now, "day": now.date()})


def test_other_unserialisable_data_is_still_a_clean_error(tmp_path):
    fails(GOOD_CODE, tmp_path, "not JSON-serialisable", data={"x": object()})


def test_print_does_not_corrupt_response(tmp_path):
    assert run("print('hello')\n" + GOOD_CODE, tmp_path).size == (648, 480)


def test_cwd_is_package_dir(tmp_path):
    fails("""
        import os
        def render(c, d, s):
            raise RuntimeError(os.getcwd())
        """, tmp_path, str(tmp_path.resolve()))


def test_env_secrets_not_inherited(tmp_path, monkeypatch):
    monkeypatch.setenv("INK_SECRET_TOKEN", "hunter2")
    monkeypatch.setenv("PYTHONPATH", "/nonexistent")
    fails("""
        import os
        def render(c, d, s):
            raise RuntimeError(sorted(os.environ))
        """, tmp_path, r"^(?!.*(INK_SECRET_TOKEN|PYTHONPATH)).*$")


def test_cwd_not_on_sys_path(tmp_path):
    (tmp_path / "evil.py").write_text("raise SystemExit('imported')")
    fails("""
        def render(c, d, s):
            import evil
        """, tmp_path, "ModuleNotFoundError")


# ---- failures ---------------------------------------------------------------------------------------------
def test_timeout_busy_loop(tmp_path):
    t = time.monotonic()
    fails("while True: pass\n", tmp_path, r"timed out after 0\.5s", timeout=0.5)
    assert time.monotonic() - t < 4


def test_timeout_sleep(tmp_path):
    fails("import time\ntime.sleep(60)\n", tmp_path, "timed out", timeout=0.5)


def test_timeout_kills_the_process(tmp_path):
    marker = tmp_path / "pid"
    # the child must be gone once run_code returns
    fails(f"import os\nopen({str(marker)!r}, 'w').write(str(os.getpid()))\nwhile True: pass\n", tmp_path, "timed out",
          timeout=0.5)
    pid = int(marker.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_hard_crash_exit_code(tmp_path):
    fails("import os\nos._exit(3)\n", tmp_path, "exit code 3")


def test_segfault_signal(tmp_path):
    fails("import os, signal\nos.kill(os.getpid(), signal.SIGSEGV)\n", tmp_path, "killed by SIGSEGV")


def test_stderr_tail_in_error(tmp_path):
    fails("import os, sys\nsys.stderr.write('last words')\nsys.stderr.flush()\nos._exit(2)\n", tmp_path, "exit code 2: last words")


def test_exception_in_render(tmp_path):
    fails("def render(c, d, s):\n    raise ValueError('boom')\n", tmp_path, "ValueError: boom")


def test_syntax_error(tmp_path):
    fails("def render(:\n", tmp_path, "SyntaxError")


def test_no_render_function(tmp_path):
    fails("x = 1\n", tmp_path, "defines no render")


def test_sys_exit_inside_render(tmp_path):
    fails("import sys\ndef render(c, d, s):\n    sys.exit(0)\n", tmp_path, "SystemExit")


def test_render_returns_wrong_type(tmp_path):
    fails("def render(c, d, s):\n    return 'text'\n", tmp_path, "must return PNG bytes or a PIL image, got str")


def test_garbage_output(tmp_path):
    fails(returning(b"this is not a png"), tmp_path, "not a PNG")


def test_empty_output(tmp_path):
    fails(returning(b""), tmp_path, "not a PNG")


def test_wrong_format(tmp_path):
    fails(returning(png(fmt="JPEG", mode="L")), tmp_path, "output is JPEG, expected PNG")


@pytest.mark.parametrize("size", [(100, 100), (480, 648), (649, 480)])
def test_wrong_size(tmp_path, size):
    fails(returning(png(size)), tmp_path, rf"image is {size[0]}x{size[1]}, expected 648x480")


@pytest.mark.parametrize("mode", ["L", "RGB"])
def test_not_one_bit(tmp_path, mode):
    fails(returning(png(mode=mode)), tmp_path, f"image mode is '{mode}', expected 1-bit")


def test_truncated_png(tmp_path):
    b = io.BytesIO()
    Image.frombytes("1", (648, 480), os.urandom(38880)).save(b, "PNG")   # noise: does not compress away
    fails(returning(b.getvalue()[:len(b.getvalue()) // 2]), tmp_path, "PNG data is corrupt")


def test_raw_garbage_on_stdout(tmp_path):
    fails("import os\nos.write(1, b'garbage')\n" + GOOD_CODE, tmp_path, "not a valid response")


def test_stdout_flood_is_bounded(tmp_path):
    fails("import os\nwhile True:\n    os.write(1, b'x' * 65536)\n", tmp_path, "screen.py:")


def test_stderr_flood_is_bounded(tmp_path):
    fails("import sys\nwhile True:\n    sys.stderr.write('x' * 65536)\n", tmp_path, "screen.py:")


def test_memory_limit(tmp_path):
    fails("def render(c, d, s):\n    return bytearray(1024**3)\n", tmp_path, "MemoryError", memory_mb=256)


def test_cannot_fork(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("RLIMIT_NPROC is not enforced for root")
    fails("import os\ndef render(c, d, s):\n    os.fork()\n", tmp_path, "BlockingIOError|OSError|Resource")


def test_file_size_limit(tmp_path):
    fails("""
        def render(c, d, s):
            with open('big.bin', 'wb') as f:
                f.write(b'x' * (8 * 1024 * 1024))
        """, tmp_path, "File too large|OSError")


def test_limits_cannot_be_raised(tmp_path):
    fails("""
        import resource
        def render(c, d, s):
            resource.setrlimit(resource.RLIMIT_AS, (resource.RLIM_INFINITY, resource.RLIM_INFINITY))
        """, tmp_path, "ValueError|not allowed|PermissionError")


# ---- inputs the host controls -------------------------------------------------------------------------------
def test_code_not_utf8(tmp_path):
    with pytest.raises(PackageError, match="not valid UTF-8"):
        run_code(b"\xff\xfe", tmp_path, {}, {})


def test_data_not_json(tmp_path):
    with pytest.raises(PackageError, match="not JSON-serialisable"):
        run_code(GOOD_CODE.encode(), tmp_path, {"x": object()}, {})


def test_interpreter_missing(tmp_path):
    with pytest.raises(PackageError, match="cannot start"):
        run_code(GOOD_CODE.encode(), tmp_path, {}, {}, python="/nonexistent/python")


def test_no_interpreter_found(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "find_python", lambda: None)
    with pytest.raises(PackageError, match="no Python interpreter"):
        run_code(GOOD_CODE.encode(), tmp_path, {}, {})


def test_find_python(monkeypatch):
    assert S.find_python() == sys.executable
    monkeypatch.setattr(sys, "frozen", True, raising=False)   # inside Decky sys.executable is the loader
    monkeypatch.setattr(S.shutil, "which", lambda name: f"/usr/bin/{name}")
    assert S.find_python() == "/usr/bin/python3"


def test_describe_exit():
    assert S._describe_exit(-9) == "killed by SIGKILL" and S._describe_exit(-200) == "killed by signal 200"
    assert S._describe_exit(1) == "exit code 1"


@pytest.mark.parametrize("body", [b"[]", b'{"ok": true}', b'{"ok": true, "png": "!!!"}', b"nope"])
def test_malformed_responses(tmp_path, monkeypatch, body):
    """A response that is JSON but not shaped right (only reachable by bypassing _runner)."""
    runner = tmp_path / "r.py"
    runner.write_text(f"import sys\nsys.stdout.write({body.decode()!r})\n")
    monkeypatch.setattr(S, "RUNNER", runner)
    with pytest.raises(PackageError, match="not a valid response"):
        run_code(GOOD_CODE.encode(), tmp_path, {}, {})


def test_response_too_large(tmp_path, monkeypatch):
    runner = tmp_path / "r.py"
    runner.write_text("import sys\nsys.stdout.write('x' * 1048577)\n")
    monkeypatch.setattr(S, "RUNNER", runner)
    with pytest.raises(PackageError, match="output larger than"):
        run_code(GOOD_CODE.encode(), tmp_path, {}, {})


def test_error_response_without_text(tmp_path, monkeypatch):
    runner = tmp_path / "r.py"
    runner.write_text("import sys\nsys.stdout.write('{\"ok\": false}')\n")
    monkeypatch.setattr(S, "RUNNER", runner)
    with pytest.raises(PackageError, match="not a valid response"):
        run_code(GOOD_CODE.encode(), tmp_path, {}, {})


# ---- error frame -------------------------------------------------------------------------------------------
def test_error_frame():
    im = error_frame("My Screen", "ValueError: " + "boom " * 400)
    assert im.size == (648, 480) and im.mode == "1"
    assert im.convert("L").tobytes().count(0) > 1000   # border + text
    assert error_frame("t", "x").tobytes() != error_frame("t", "y").tobytes()


# ---- _runner in-process (coverage; the subprocess tests above prove the real thing) ------------------------
def req(code, **kw):
    return {"code": textwrap.dedent(code), "path": "screen.py", "size": [648, 480], "data": {}, "settings": {},
            "sys_path": [], **kw}


def test_runner_render_request_ok():
    r = R.render_request(req(GOOD_CODE))
    assert r["ok"] and Image.open(io.BytesIO(base64.b64decode(r["png"]))).size == (648, 480)


def test_runner_render_request_pil_image():
    r = R.render_request(req("from PIL import Image\ndef render(c, d, s): return Image.new('1', c, 0)"))
    assert Image.open(io.BytesIO(base64.b64decode(r["png"]))).mode == "1"


def test_runner_render_request_errors():
    assert "boom" in R.render_request(req("def render(c,d,s): raise KeyError('boom')"))["error"]
    assert "no render" in R.render_request(req("pass"))["error"]
    assert "got int" in R.render_request(req("def render(c,d,s): return 1"))["error"]
    assert len(R.render_request(req("def render(c,d,s): raise ValueError('x'*1000)"))["error"]) == 300


def test_runner_apply_limits(monkeypatch):
    import resource
    calls = []

    def fake(which, limits):
        calls.append((which, limits))
        if which == resource.RLIMIT_NPROC:
            raise ValueError("not permitted")   # best effort: must not abort

    monkeypatch.setattr(resource, "setrlimit", fake)
    R.apply_limits({"memory_mb": 100, "cpu_s": 3, "fsize_mb": 2})
    got = dict(calls)
    assert got[resource.RLIMIT_AS] == (100 * 1024 * 1024,) * 2
    assert got[resource.RLIMIT_CPU] == (3, 3) and got[resource.RLIMIT_FSIZE] == (2 * 1024 * 1024,) * 2
    assert got[resource.RLIMIT_NPROC] == (0, 0) and got[resource.RLIMIT_CORE] == (0, 0)
    assert all(lo == hi for lo, hi in got.values())   # soft == hard: cannot be raised again


def test_runner_main(monkeypatch):
    class In:
        buffer = io.BytesIO(json.dumps({**req(GOOD_CODE), "limits": {}}).encode())

    out = io.StringIO()
    monkeypatch.setattr(sys, "stdin", In)
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(R, "apply_limits", lambda limits: None)
    R.main()
    assert json.loads(out.getvalue())["ok"] is True
