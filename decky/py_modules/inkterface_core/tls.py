"""The one TLS context every HTTPS client in the plugin uses. Stdlib only.

Why it exists: Decky Loader runs plugins in its own PyInstaller Python, whose OpenSSL was built with the default CA
location /usr/lib/ssl. SteamOS has no such directory, so `ssl.create_default_context()` there trusts nothing and every
request dies with CERTIFICATE_VERIFY_FAILED (docs/troubleshooting.md).

Verification is always on (CERT_REQUIRED + check_hostname, from `ssl.create_default_context`). Only the trust anchors
are chosen, first one that loads wins:
  1. $SSL_CERT_FILE (an explicit user/admin choice)
  2. the OS bundle (SteamOS / Arch: /etc/ssl/certs/ca-certificates.crt, /etc/ca-certificates/extracted/tls-ca-bundle.pem)
  3. the certifi bundle shipped in the zip (py_modules/certifi/cacert.pem, pinned in vendor-requirements.txt)
  4. nothing found: the interpreter's own default paths (verification still on, so it fails closed)
The vendored bundle is read by path, never imported: Decky's frozen process already holds its own `certifi` module.
"""
from __future__ import annotations

import os
import ssl
from pathlib import Path

OS_CA_FILES = ("/etc/ssl/certs/ca-certificates.crt", "/etc/ca-certificates/extracted/tls-ca-bundle.pem")
VENDORED_CA = Path(__file__).resolve().parents[1] / "certifi" / "cacert.pem"


def make_ssl_context(env=os.environ, os_files=OS_CA_FILES, vendored=VENDORED_CA) -> ssl.SSLContext:
    """A verifying client context. The three keyword arguments exist for tests."""
    candidates = [env.get("SSL_CERT_FILE") or "", *os_files, str(vendored)]
    for path in candidates:
        if not path or not os.path.isfile(path):
            continue
        try:
            return ssl.create_default_context(cafile=path)   # an empty or corrupt bundle raises: try the next one
        except (OSError, ssl.SSLError):
            continue
    return ssl.create_default_context()


def describe(exc: BaseException) -> str:
    """One user-facing line for a failed request; a certificate failure gets the reason and what to check."""
    seen = set()
    e: BaseException | None = exc
    while e is not None and id(e) not in seen:   # URLError.reason / __cause__ / __context__ nest the real error
        seen.add(id(e))
        if isinstance(e, ssl.SSLCertVerificationError):
            return (f"TLS certificate check failed ({e.verify_message or e}). Check the Steam Machine's date and time, "
                    "and that no proxy or captive portal is rewriting HTTPS.")
        nxt = getattr(e, "reason", None)
        e = nxt if isinstance(nxt, BaseException) else (e.__cause__ or e.__context__)
    reason = getattr(exc, "reason", exc)
    return str(reason)
