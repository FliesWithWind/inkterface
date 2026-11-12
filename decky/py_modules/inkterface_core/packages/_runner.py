"""Child process for code-tier screens: `python -I _runner.py`. Standard library only, no relative imports.

Reads ONE JSON request on stdin, writes ONE JSON response on stdout:

    request  {"code": str, "path": str, "size": [w, h], "data": {...}, "settings": {...},
              "sys_path": [str], "limits": {"memory_mb": int, "cpu_s": int, "fsize_mb": int}}
    response {"ok": true, "png": "<base64>"}  or  {"ok": false, "error": "<text>"}

`print()` from screen.py goes to stderr. The limits are a guardrail against buggy screens, NOT a sandbox.
"""
import base64
import io
import json
import sys

MB = 1024 * 1024


def apply_limits(limits: dict) -> None:
    """Lower (soft = hard, so screen.py cannot raise them again) what a runaway screen can consume."""
    import resource

    wanted = (
        ("RLIMIT_AS", limits["memory_mb"] * MB),   # address space: allocation bombs raise MemoryError
        ("RLIMIT_CPU", limits["cpu_s"]),           # busy loops get SIGXCPU
        ("RLIMIT_FSIZE", limits["fsize_mb"] * MB), # bounds files screen.py (and our stdout/stderr files) may grow
        ("RLIMIT_NPROC", 0),                       # no fork/threads; not enforced for root
        ("RLIMIT_NOFILE", 64),
        ("RLIMIT_CORE", 0),
    )
    for name, value in wanted:
        try:
            resource.setrlimit(getattr(resource, name), (value, value))
        except (AttributeError, ValueError, OSError):
            pass  # best effort: e.g. RLIMIT_AS is not enforced on macOS


def render_request(req: dict) -> dict:
    try:
        sys.path.extend(req["sys_path"])   # appended: stdlib and the system Pillow win, vendored Pillow is the fallback
        ns = {"__name__": "screen", "__file__": req["path"]}
        exec(compile(req["code"], req["path"], "exec"), ns)
        render = ns.get("render")
        if not callable(render):
            return {"ok": False, "error": "screen.py defines no render(canvas_size, data, settings)"}
        out = render(tuple(req["size"]), req["data"], req["settings"])
        if hasattr(out, "save"):                 # a PIL image: convenience for authors
            buf = io.BytesIO()
            out.save(buf, "PNG")
            out = buf.getvalue()
        if not isinstance(out, (bytes, bytearray)):
            return {"ok": False, "error": f"render() must return PNG bytes or a PIL image, got {type(out).__name__}"}
        return {"ok": True, "png": base64.b64encode(bytes(out)).decode("ascii")}
    except BaseException as e:  # noqa: BLE001 - includes SystemExit: the parent always gets an answer
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}


def main() -> None:
    req = json.loads(sys.stdin.buffer.read())
    out = sys.stdout
    sys.stdout = sys.stderr    # stray print() must not corrupt the response
    apply_limits(req["limits"])
    out.write(json.dumps(render_request(req)))
    out.flush()


if __name__ == "__main__":  # pragma: no cover
    main()
