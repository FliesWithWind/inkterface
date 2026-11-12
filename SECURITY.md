# Security policy

## Reporting a problem

Use GitHub's private vulnerability reporting: the repository's **Security** tab, then **Report a vulnerability**.
Do not open a public issue with exploit details. If that button is missing, open a public issue that says only that you
have a security report and ask for a private way to send it.

Useful in a report: the plugin and firmware versions (Plugin page, About), what you did, what you expected, what
happened. This is a hobby-scale project: expect a reply in days, not hours. Supported: the newest release only.

## What is protected, and what is not

### Firmware updates (OTA)

Full design: [docs/ota.md](docs/ota.md). The short version:

- Releases are signed with an **Ed25519** key. The plugin only accepts a `manifest.json` whose signature matches the
  public key bundled in `decky/py_modules/inkterface_core/ota/trust.py`. The signature is checked before the manifest
  is parsed; the manifest carries the sha256 and size of the image, which are checked after the download and again by
  the panel from flash.
- **The private key is held by the project maintainer.** It is never in the repository. CI gets it as the repository
  secret `INKTERFACE_SIGNING_KEY` and uses it only to sign the manifest of a tagged release.
- **Fail closed:** with no public key bundled (the state of a fresh checkout), every release is refused and the plugin
  makes no network request for updates. Forks and source builds must bundle their own key.
- No downgrade, no replay: an older release is refused, and the signed version has to equal the release tag.
- The plugin never installs by itself. A user taps Install. A panel that does not confirm the new image within about
  3 minutes rolls back to the previous firmware.
- **Not covered:** the panel itself does not verify signatures, and secure boot and flash encryption are off. Anything
  that can open the panel's USB serial port can flash it. A leaked signing key would let an attacker publish updates
  that every plugin installs; rotation needs a new plugin release with the new public key.

### Code screens are not sandboxed

A screen is JSON, and optionally one `screen.py`. The Python file runs as the same user as the plugin, in a separate
process with limits (empty environment, time, memory and file-size caps). **This is a guardrail against buggy screens,
not a sandbox**: no filesystem or network isolation. The protection is the approval gate: a code screen stays disabled
until the user approves the exact bytes (author, version and SHA-256 are shown), and any change to the code needs a new
approval. Only approve code you have read or trust. Details and limits: [docs/screen-packages.md](docs/screen-packages.md).
The plugin does not run as root, so an approved screen does not either. Do not add the `root` flag to `plugin.json`.

### Secrets

- **Steam Web API key:** kept in the plugin's settings directory (`host.json`, mode 0600, directory 0700). It is never
  logged, never returned to the UI (which only sees "set" or "unset"), never put in an event or an error message, and
  never passed to a code screen. The only places that use it are the Steam Web API source and the panel's idle mode.
- **Wi-Fi password and Steam key on the panel:** sent over USB to the panel's flash for idle mode. **They are stored
  unencrypted** (flash encryption is not enabled) and the panel never logs them. Anyone with physical access to the chip
  can read them. Use a Steam key and a Wi-Fi network you can afford to rotate.
- **Panel TLS does not validate certificates** (`setInsecure()`): on a hostile network the Steam key and the displayed
  data can be read or spoofed. See [docs/idle-mode.md](docs/idle-mode.md#security-and-known-limits).
- Panel backups made with `esptool read_flash` contain those secrets. Keep them private and never commit them.

### Other boundaries

- The plugin finds the panel by USB vendor id `303a` and never opens other serial devices (on a Steam Machine, the
  `ttyACM` ports of game controllers).
- Screen packages are validated before use: https only, size and entry caps, no path tricks, no symlinks.
- Downloaded firmware must come from `https://github.com/<repo>/releases/download/<tag>/<asset>`; any other URL is
  refused.
- Tests: [docs/ota.md](docs/ota.md#tests) and the suites under `decky/tests/` cover the signature, manifest, download
  and screen-package checks.
