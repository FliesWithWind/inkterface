"""inkterface_core.tls: which CA bundle is trusted (selection order), that verification can never be switched off, and
all three HTTPS clients (OTA/GitHub, package+catalog, sources) against a real local TLS server with a throwaway CA."""
import datetime
import ipaddress
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from inkterface_core import tls
from inkterface_core.ota import github
from inkterface_core.ota.errors import NetworkError
from inkterface_core.packages import PackageError, urllib_fetch as pkg_fetch
from inkterface_core.sources.netfetch import FetchError, urllib_fetch as src_fetch

NOW = datetime.datetime.now(datetime.timezone.utc)


def _name(cn):
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def make_ca(cn):
    key = ec.generate_private_key(ec.SECP256R1())
    cert = (x509.CertificateBuilder().subject_name(_name(cn)).issuer_name(_name(cn)).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(NOW - datetime.timedelta(days=1))
            .not_valid_after(NOW + datetime.timedelta(days=2))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .sign(key, hashes.SHA256()))
    return cert, key


def make_leaf(ca, host_ip="127.0.0.1"):
    ca_cert, ca_key = ca
    key = ec.generate_private_key(ec.SECP256R1())
    cert = (x509.CertificateBuilder().subject_name(_name("leaf")).issuer_name(ca_cert.subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(NOW - datetime.timedelta(days=1))
            .not_valid_after(NOW + datetime.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(host_ip))]), critical=False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))
    return cert, key


def pem(cert):
    return cert.public_bytes(serialization.Encoding.PEM)


def write_ca(path, cn):
    ca = make_ca(cn)
    path.write_bytes(pem(ca[0]))
    return ca


def subjects(ctx):
    return {d["subject"][0][0][1] for d in ctx.get_ca_certs()}


def check_verifying(ctx):
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname is True


# --- selection order ------------------------------------------------------------------------------------------------

@pytest.fixture
def bundles(tmp_path):
    """env, os1, os2, vendored bundles, each a one-certificate PEM with its own CN."""
    out = {}
    for n in ("env", "os1", "os2", "vendored"):
        write_ca(tmp_path / f"{n}.pem", n)
        out[n] = tmp_path / f"{n}.pem"
    return out


def ctx_for(b, *, env=True, os1=True, os2=True, vendored=True):
    return tls.make_ssl_context(
        env={"SSL_CERT_FILE": str(b["env"])} if env else {},
        os_files=tuple(str(b[k]) for k, on in (("os1", os1), ("os2", os2)) if on),
        vendored=b["vendored"] if vendored else b["vendored"].with_name("absent.pem"))


def test_env_var_wins_over_everything(bundles):
    assert subjects(ctx_for(bundles)) == {"env"}


def test_os_bundle_when_no_env_var(bundles):
    assert subjects(ctx_for(bundles, env=False)) == {"os1"}


def test_second_os_path_when_the_first_is_absent(bundles):
    assert subjects(ctx_for(bundles, env=False, os1=False)) == {"os2"}


def test_vendored_certifi_when_the_os_has_none(bundles):
    assert subjects(ctx_for(bundles, env=False, os1=False, os2=False)) == {"vendored"}


def test_env_var_pointing_nowhere_falls_through_to_the_os(bundles):
    bundles["env"].unlink()
    assert subjects(ctx_for(bundles)) == {"os1"}


def test_corrupt_or_empty_bundle_is_skipped_not_trusted(bundles):
    bundles["env"].write_text("this is not a PEM file")
    bundles["os1"].write_bytes(b"")
    assert subjects(ctx_for(bundles)) == {"os2"}


def test_empty_env_var_is_ignored(bundles):
    c = tls.make_ssl_context(env={"SSL_CERT_FILE": ""}, os_files=(str(bundles["os1"]),), vendored=bundles["vendored"])
    assert subjects(c) == {"os1"}


def test_no_bundle_anywhere_still_verifies(bundles):
    c = ctx_for(bundles, env=False, os1=False, os2=False, vendored=False)   # interpreter defaults: fails closed
    check_verifying(c)


@pytest.mark.parametrize("kw", [dict(), dict(env=False), dict(env=False, os1=False, os2=False),
                                dict(env=False, os1=False, os2=False, vendored=False)])
def test_verification_is_always_on(bundles, kw):
    check_verifying(ctx_for(bundles, **kw))


def test_the_shipped_certifi_bundle_loads_with_the_real_defaults():
    assert tls.VENDORED_CA.is_file()
    c = tls.make_ssl_context(env={}, os_files=())
    check_verifying(c)
    assert len(c.get_ca_certs()) > 100


def test_real_defaults_verify():
    check_verifying(tls.make_ssl_context())


def test_cert_none_is_never_used():
    import inspect
    assert "CERT_NONE" not in inspect.getsource(tls) and "_create_unverified_context" not in inspect.getsource(tls)


# --- describe ------------------------------------------------------------------------------------------------------

def test_describe_certificate_failure_is_actionable():
    import urllib.error
    inner = ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")
    inner.verify_message = "unable to get local issuer certificate"
    chained = OSError("wrapped")
    chained.__cause__ = inner
    for exc in (inner, urllib.error.URLError(inner), chained):
        msg = tls.describe(exc)
        assert "TLS certificate check failed" in msg and "unable to get local issuer certificate" in msg
        assert "date and time" in msg


def test_describe_other_errors_keep_their_text():
    import urllib.error
    assert tls.describe(urllib.error.URLError("dns down")) == "dns down"
    assert tls.describe(TimeoutError("slow")) == "slow"


# --- real TLS server, throwaway CA, all three clients ---------------------------------------------------------------

class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def server(tmp_path):
    """HTTPS on 127.0.0.1 with a leaf signed by a throwaway CA. Returns (url, ca_pem_path)."""
    ca = make_ca("throwaway test CA")
    leaf_cert, leaf_key = make_leaf(ca)
    (tmp_path / "leaf.pem").write_bytes(pem(leaf_cert))
    (tmp_path / "leaf.key").write_bytes(leaf_key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    sctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    sctx.load_cert_chain(tmp_path / "leaf.pem", tmp_path / "leaf.key")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
    httpd.socket = sctx.wrap_socket(httpd.socket, server_side=True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    (tmp_path / "ca.pem").write_bytes(pem(ca[0]))
    yield f"https://127.0.0.1:{httpd.server_address[1]}/x", tmp_path / "ca.pem"
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture
def clients():
    return {
        "ota": lambda u: github.urllib_fetch(u, 1000).body,
        "packages": lambda u: pkg_fetch(u, 1000, 5),
        "sources": lambda u: src_fetch(u, 5),
    }


@pytest.mark.parametrize("name", ["ota", "packages", "sources"])
def test_trusted_ca_is_accepted(server, clients, monkeypatch, name):
    url, ca = server
    monkeypatch.setenv("SSL_CERT_FILE", str(ca))
    assert clients[name](url) == b'{"ok": true}'


@pytest.mark.parametrize("name,exc", [("ota", NetworkError), ("packages", PackageError), ("sources", FetchError)])
def test_untrusted_ca_gives_a_clear_error(server, clients, monkeypatch, name, exc):
    url, _ = server
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)   # real OS / certifi store: the throwaway CA is in neither
    with pytest.raises(exc) as e:
        clients[name](url)
    assert "TLS certificate check failed" in str(e.value)


def test_hostname_mismatch_is_rejected_even_with_a_trusted_ca(server, monkeypatch):
    url, ca = server
    monkeypatch.setenv("SSL_CERT_FILE", str(ca))
    with pytest.raises(NetworkError, match="TLS certificate check failed"):
        github.urllib_fetch(url.replace("127.0.0.1", "localhost"), 1000)   # the leaf is only valid for the IP
