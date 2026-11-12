"""The one HTTP entry point of the sources package. Every network source takes a `fetch` callable
(`fetch(url, timeout) -> bytes`, raises FetchError) so tests inject a fake and never touch the network."""
from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.request

from .. import tls
from .base import SourceError

MAX_BYTES = 8 * 1024 * 1024  # a 5000-game GetOwnedGames with app info is ~1.5 MB


class FetchError(Exception):
    """Transport failure. `status` is the HTTP status when there was one. Never contains the URL (it may hold an API key)."""

    def __init__(self, msg: str, status: int | None = None):
        super().__init__(msg)
        self.status = status


def urllib_fetch(url: str, timeout: float = 5.0) -> bytes:
    if not url.startswith(("https://", "http://")):
        raise FetchError("unsupported URL scheme")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "inkterface-decky/1", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout, context=tls.make_ssl_context()) as r:
            data = r.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as e:
        raise FetchError(f"HTTP {e.code}", e.code) from None
    except (OSError, http.client.HTTPException, ValueError) as e:  # URLError, timeouts, TLS, bad URL, truncated reply
        raise FetchError(f"{type(e).__name__}: {tls.describe(e)}") from None
    if len(data) > MAX_BYTES:
        raise FetchError("response too large")
    return data


def fetch_json(fetch, url: str, timeout: float):
    """GET + parse. Raises SourceError (message free of the URL) on transport or parse failure."""
    try:
        data = fetch(url, timeout)
    except FetchError as e:
        raise SourceError(str(e), e.status) from None
    try:
        return json.loads(data)
    except ValueError:  # JSONDecodeError and UnicodeDecodeError
        raise SourceError("malformed JSON") from None
