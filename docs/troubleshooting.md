# Troubleshooting

## `CERTIFICATE_VERIFY_FAILED` / "TLS certificate check failed"

Symptom: the Firmware page, the screen catalog or a package URL import shows `cannot reach GitHub: ... CERTIFICATE_VERIFY_FAILED
... unable to get local issuer certificate`, or the plugin's own line `TLS certificate check failed (unable to get local issuer
certificate)`.

Cause: Decky runs plugins in a frozen Python with its own OpenSSL, built with the Debian CA location `/usr/lib/ssl`. SteamOS has
no such directory, so Decky's default SSL context loads no trust anchors and every server fails verification.

What the plugin does: all HTTPS clients (OTA, package download and catalog, data sources) use one shared
`inkterface_core/tls.py`. It builds a context that always verifies (`CERT_REQUIRED`, `check_hostname=True`; it cannot be switched
off) using the first trust store that exists and loads:

1. `$SSL_CERT_FILE`
2. `/etc/ssl/certs/ca-certificates.crt`
3. `/etc/ca-certificates/extracted/tls-ca-bundle.pem`
4. the vendored certifi bundle `py_modules/certifi/cacert.pem` (read by path, not imported, because Decky's process has its own
   `certifi` module)
5. none found: the interpreter defaults, which still verify and so fail closed

How to check: with the TLS stack Decky uses, a default `ssl.create_default_context()` reports `cafile=None, capath=None` and
fails; `inkterface_core.tls.make_ssl_context()` verifies `api.github.com` and `github.com`. To
try it by hand, run Python with the plugin's `py_modules` on the path and open `https://api.github.com/` with
`urllib.request.urlopen(url, context=make_ssl_context())`. Any HTTP status in the reply means the handshake and certificate check passed.

If the error persists after an update, check the Steam Machine's date and time and that no proxy or captive portal rewrites
HTTPS. A `403` from `api.github.com` is GitHub's unauthenticated rate limit (60 requests per hour per IP), not a TLS problem; the
Firmware page shows the reset time.

## Serial permission

`Permission denied` in the status line means the user has no ACL on `/dev/ttyACM*`. See "Serial permission" in
[using.md](using.md) for the udev rule. Do not run the plugin as root.

## Panel not found

The plugin finds the panel by its USB vendor id (Espressif `303a`) and never opens other serial devices. If it does not appear,
check the USB cable (it must carry data), replug the panel, and see [flashing.md](flashing.md) for the serial port path and
flashing the firmware.
