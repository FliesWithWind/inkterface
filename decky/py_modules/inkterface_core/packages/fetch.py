"""The one place that touches the network: download a package zip over https."""
from __future__ import annotations

import time
import urllib.error
import urllib.request
from collections.abc import Callable

from .. import tls
from .extract import PackageError

DEFAULT_TIMEOUT_S = 15.0
Fetch = Callable[[str, int, float], bytes]   # (url, max_bytes, timeout_s) -> body; raises PackageError


class _HttpsOnly(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.lower().startswith("https://"):
            raise PackageError(f"{req.full_url}: redirect to a non-https URL refused")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def urllib_fetch(url: str, max_bytes: int, timeout: float = DEFAULT_TIMEOUT_S) -> bytes:
    """GET `url`. `timeout` bounds the whole download (not just one socket read); `max_bytes` bounds the body."""
    deadline = time.monotonic() + timeout
    chunks: list[bytes] = []
    total = 0
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "inkterface-packages"})
        with urllib.request.build_opener(_HttpsOnly, urllib.request.HTTPSHandler(context=tls.make_ssl_context())).open(req, timeout=timeout) as r:
            length = r.headers.get("Content-Length", "")
            if length.isdigit() and int(length) > max_bytes:
                raise PackageError(f"{url}: download is {length} bytes (max {max_bytes})")
            while chunk := r.read1(64 * 1024):
                total += len(chunk)
                if total > max_bytes:
                    raise PackageError(f"{url}: download exceeds {max_bytes} bytes")
                if time.monotonic() > deadline:
                    raise PackageError(f"{url}: download timed out after {timeout:g}s")
                chunks.append(chunk)
    except (urllib.error.URLError, OSError, ValueError) as e:   # HTTPError, socket timeouts, bad URL
        why = e if isinstance(e, urllib.error.HTTPError) else tls.describe(e)
        raise PackageError(f"{url}: download failed: {why}") from None
    return b"".join(chunks)
