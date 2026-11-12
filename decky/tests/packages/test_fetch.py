"""urllib_fetch against a real local HTTP server (the https-only rule lives in the store; redirects are checked here)."""
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from inkterface_core.packages import PackageError, urllib_fetch
from inkterface_core.packages.fetch import _HttpsOnly


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        try:
            self.route()
        except (BrokenPipeError, ConnectionResetError):
            pass   # the client gave up, which is what the tests want

    def route(self):
        p = self.path
        if p == "/ok":
            body = b"zipdata" * 10
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif p == "/declared-big":
            self.send_response(200)
            self.send_header("Content-Length", str(50 * 1024 * 1024))
            self.end_headers()
            self.wfile.write(b"x")
        elif p == "/stream-big":   # no Content-Length: body ends when the connection closes
            self.send_response(200)
            self.end_headers()
            for _ in range(100):
                self.wfile.write(b"x" * 8192)
        elif p == "/slow":
            self.send_response(200)
            self.end_headers()
            for _ in range(100):
                self.wfile.write(b"x")
                self.wfile.flush()
                time.sleep(0.1)
        elif p == "/stall":
            self.send_response(200)
            self.end_headers()
            time.sleep(3)
        elif p == "/redirect":
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/ok")
            self.end_headers()
        else:
            self.send_error(404)


@pytest.fixture(scope="module")
def base():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_ok(base):
    assert urllib_fetch(base + "/ok", 1000, 5) == b"zipdata" * 10


def test_declared_size_over_cap(base):
    with pytest.raises(PackageError, match="download is 52428800 bytes"):
        urllib_fetch(base + "/declared-big", 1000, 5)


def test_streamed_body_over_cap(base):
    with pytest.raises(PackageError, match="exceeds 100000 bytes"):
        urllib_fetch(base + "/stream-big", 100_000, 5)


def test_total_deadline_beats_slow_drip(base):
    t = time.monotonic()
    with pytest.raises(PackageError, match="timed out after 0.5s"):
        urllib_fetch(base + "/slow", 10_000, 0.5)
    assert time.monotonic() - t < 3


def test_stalled_server_hits_socket_timeout(base):
    with pytest.raises(PackageError, match="download failed"):
        urllib_fetch(base + "/stall", 10_000, 0.5)


def test_http_error(base):
    with pytest.raises(PackageError, match="404"):
        urllib_fetch(base + "/missing", 1000, 5)


def test_redirect_to_http_refused(base):
    with pytest.raises(PackageError, match="redirect to a non-https URL refused"):
        urllib_fetch(base + "/redirect", 1000, 5)


def test_redirect_to_https_allowed():
    req = urllib.request.Request("https://a.example/x")
    out = _HttpsOnly().redirect_request(req, None, 302, "Found", {}, "https://b.example/y")
    assert out.full_url == "https://b.example/y"


def test_connection_refused():
    with pytest.raises(PackageError, match="download failed"):
        urllib_fetch("http://127.0.0.1:1/x", 1000, 2)


def test_malformed_url():
    with pytest.raises(PackageError, match="download failed"):
        urllib_fetch("not a url", 1000, 2)
