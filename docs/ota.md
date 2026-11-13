# Signed firmware OTA over USB

The Decky plugin finds a firmware release on GitHub, verifies its ed25519 signature, and on
the user's tap streams the image over the USB cable into the panel's inactive OTA slot. The
panel checks the hash, reboots into the new image, and keeps it only if the host confirms a
good handshake. Otherwise the bootloader rolls back. No Wi-Fi is involved.

Wire protocol: [`usb-protocol-v1.md`](usb-protocol-v1.md) section "OTA".
Code: host `decky/py_modules/inkterface_core/ota/`, panel `firmware/src/ee05/ota_usb.{h,cpp}`,
release tools `tools/release/`, workflows `.github/workflows/`.

## Flow

1. Plugin start, then daily: `github.find_release(...)` lists releases of
   `FliesWithWind/inkterface`, takes the newest `fw-v<semver>`, downloads `manifest.json` and
   `manifest.sig`, verifies, applies policy. Result: a `Release` (changelog, version) or `None`.
   The plugin never installs by itself.
2. User taps Install: `github.download_image` (size cap, size and sha256 check), then
   `stream.install(open_panel, image, version, progress=..., cancel=...)`:
   OTA_BEGIN, windowed OTA_DATA, OTA_END, reconnect after the panel reboots,
   `confirm_boot` (HELLO shows the new `fw_ver`, then the HELLO ack).
3. Panel: sha256 of what is really in flash, ESP image validation, boot partition flip, ACK,
   reboot ~400 ms later. After the ack the image is valid. No ack within 180 s: rollback.

GitHub rate limit (60 unauthenticated API requests per hour per public IP, shared by everything behind it, Decky Loader and
other plugins included). Every API request counts, a conditional one answered `304` as well (that is only free with a token), so the
plugin avoids requests: the release list (`per_page=20`) is cached in `<settings>/cache/fw-releases.json` (ETag, Last-Modified,
when it was fetched, a slimmed copy of the list; at most 256 KB; a missing or malformed file is ignored). The automatic check at
plugin start reuses a list younger than 24 h with no request, so restarts cost nothing; otherwise at most one request per 24 h
(1 per hour after a failure). A manual check asks GitHub (conditionally) but not twice within 60 s. On 403/429 with
`X-RateLimit-Remaining: 0` or `Retry-After`, no request is sent before the reset time (`Retry-After` wins over `X-RateLimit-Reset`;
a reset more than 2 h away is capped) and the Firmware page says when checking works again. While limited the cached list is used;
with no cache a stable plugin reads the newest release from `github.com/<repo>/releases/latest/download/manifest.{json,sig}`, which
is not API-limited (it only works while the newest non-pre-release is a firmware release). Every path ends in the same signature
check.

Plugin wiring (see [plugin-ui.md](plugin-ui.md#firmware-updates)): `inkterface_core/firmware.py` (`FirmwareManager`, owned by
`PluginCore`) checks at start and every 24 h, exposes `get_firmware_status` / `check_firmware` / `install_firmware` / `cancel_firmware_install` and
the `firmware` event, and runs the install in a worker thread while the engine is paused (`Engine.pause()` / `resume()`):

```python
rel = github.find_release(fetch, trust.public_key(), plugin_version=PLUGIN_VERSION)   # installed_fw="": the UI compares
image = github.download_image(fetch, rel)
stream.install(open_panel, image, rel.manifest.version, progress=..., cancel=cancel_event.is_set)
```

It never installs without the user's tap, shows `trust.state == "no_key"` (and makes no network request) while `trust.py` is empty, and treats a
HELLO protocol mismatch as "update the firmware here". Do not send heartbeats or RECTs while `install` runs; the engine is paused for that reason.
After `confirm_boot` returns the engine resumes. The first non-HELLO frame sent *before* `confirm_boot` accepted the version must not
exist: the ack is the only thing that validates the image.

## Release format

Tag `fw-v<semver>` (e.g. `fw-v1.2.0`; a `-beta.1` or `-rc.1` suffix makes it a GitHub pre-release, which a stable plugin ignores and a
pre-release plugin, such as 1.0.0-beta.1, also offers). Assets:

| asset | content |
|---|---|
| `inkterface-ee05-<ver>.bin` | `firmware.bin` from `pio run -e ee05` (the app image, not the merged flash image) |
| `manifest.json` | metadata, below |
| `manifest.sig` | raw 64-byte Ed25519 signature over the exact bytes of `manifest.json` |

```json
{
  "version": "1.2.0",
  "protocol_version": 1,
  "sha256": "<64 lowercase hex of the .bin>",
  "size": 1480400,
  "min_plugin_version": "0.0.1",
  "changelog": "- Faster refresh\n- ..."
}
```

Unknown fields are ignored. Duplicate keys, wrong types (a bool is not an int), `size` outside
1..0x330000, bad semver, uppercase hex: the manifest is rejected. `version` must equal the tag,
and release builds report exactly that string as `HELLO.fw_ver` (CI passes
`INKTERFACE_FW_VERSION`, `firmware/autorev.py` bakes it in; a value that is not semver
fails the build). Dev builds report `0.0.0-dev+<7-char git sha>` (`.dirty` appended for a modified
tree, `+unknown` without git) and are treated as "unknown version": any release is offered,
downgrade protection cannot apply, and the plugin UI shows "(development build)". The string never
comes from git tags. Panels flashed with older firmware report `g<describe>` (a `git describe` string); the plugin treats those as dev builds too.

`firmware/min_plugin_version.txt` feeds `min_plugin_version`. Raise it when a firmware needs
newer plugin behaviour than `protocol_version` can express. `protocol_version` must equal the
plugin's `proto.PROTO_VER`. The OTA frames and HELLO must stay compatible across protocol
versions, otherwise a plugin could not flash a panel it can no longer talk to.

## Trust model

What the plugin enforces (`ota/manifest.py`, `ota/github.py`; all covered by tests):

- **Signature first.** The Ed25519 signature over the raw manifest bytes is verified before the
  JSON is parsed. Unsigned, wrong length, wrong key, any flipped bit in manifest, signature or
  key: refused. The verifier is a vendored pure-Python RFC 8032 implementation
  (`ota/ed25519.py`, checked against the RFC 8032 section 7.1 vectors and, in CI, against the
  `cryptography` package).
- **Key bundled in the plugin** (`ota/trust.py`). Empty = fail closed: every release is refused
  until the maintainer pastes the production public key.
- **Chain to the bytes.** Manifest signs sha256 + size of the .bin; the download is capped at
  the signed size, then size and hash are checked; the panel hashes again from flash.
- **No downgrade, no replay.** A release older than the running firmware is refused; the signed
  version must equal the release tag (an old signed manifest re-attached to a new tag fails);
  the asset size listed by GitHub must equal the signed size.
- **Compatibility gates.** `protocol_version` mismatch and `min_plugin_version` above the
  plugin are refused with a specific error.
- **Download URLs** must be exactly `https://github.com/<repo>/releases/download/<tag>/<asset>`
  (no `file://`, no other host), HTTPS only, no credentials ever sent.
- **Newest release only.** If the newest `fw-v*` release is broken or unsigned the check fails
  loudly instead of quietly offering an older one. Drafts are skipped; so are pre-releases, unless the plugin is itself a pre-release (its version has a `-` suffix).

What it does not give you (be aware):

- The **panel does not verify signatures.** Anything that can open the panel's serial port can
  flash it (OTA_END only checks the hash the same sender supplied and that the image is a valid
  ESP32 app). The signature protects the update path from GitHub, not the panel from a local
  attacker. Secure boot / flash encryption are not enabled.
- **A leaked signing key** lets an attacker publish updates every plugin installs. Rotation
  needs a plugin update with the new public key. A Decky store update ships it, but panels
  cannot be forced to update.
- A malicious/compromised GitHub account can withhold or delete releases (denial of update),
  not forge them.

## Maintainer runbook

One-time setup (the production private key is held by the maintainer; it is not in the repo and the
repo has no copy):

```bash
python3 tools/release/gen_keypair.py \
    --private <path-to-private-key> --public <path-to-public-key>
# prints PRODUCTION_PUBLIC_KEY_HEX = "..."
```

1. Back the private file up somewhere offline (password manager / encrypted drive). Lose it and
   you must ship a plugin with a new key, panels already in the field keep working.
2. Paste the public hex into `decky/py_modules/inkterface_core/ota/trust.py`
   (`PRODUCTION_PUBLIC_KEY_HEX`), commit, release a plugin that contains it.
3. Set the secret (value = the 64 hex chars in the private file; it never touches disk in CI):

   ```bash
   gh secret set INKTERFACE_SIGNING_KEY --repo FliesWithWind/inkterface < <path-to-private-key>
   ```

4. **First flash is wired, once**, with the OTA-capable firmware (it also writes the bootloader
   with rollback support and the dual-slot partition table): `cd firmware && pio run -e ee05 -t upload`.
   Back up the panel first (see `usb-protocol-v1.md`, "Hardware test procedure").

The first release, step by step: [RELEASING.md](RELEASING.md). In short, cutting a firmware release:

```bash
git tag fw-v1.0.0-beta.2 && git push origin fw-v1.0.0-beta.2   # the tag must equal VERSION
```

`firmware-release.yml` then: runs the full pytest suite, builds `ee05` with
`INKTERFACE_FW_VERSION=1.0.0-beta.2` and the public example `idle_config.h` (Wi-Fi/Steam secrets come
from NVS, none are in the image), writes the changelog from `git log` since the previous
`fw-v` tag, signs with `INKTERFACE_SIGNING_KEY`, runs `tools/release/verify_release.py` with the
bundled public key (a wrong or stale secret fails here, before publishing), and creates the
release with the three assets. Locally the same steps are:

```bash
INKTERFACE_FW_VERSION=1.0.0-beta.2 pio run -e ee05    # in firmware/
python3 tools/release/sign_manifest.py firmware/.pio/build/ee05/firmware.bin \
    --version 1.0.0-beta.2 --key-file <path-to-private-key> --out-dir dist
python3 tools/release/verify_release.py dist
```

Plugin release: set `VERSION` and `decky/package.json` `version` to the same value, tag `plugin-v<same>`; `plugin-release.yml`
refuses a tag that disagrees with either, runs pytest, builds with pnpm and publishes
`inkterface-plugin-<ver>.zip`. `ci.yml` runs pytest (coverage gate 90% on `ota/` and `firmware.py`), `pio run -e ee05`,
vitest, tsc and the pnpm build on every push and PR.

The workflows are linted with `actionlint` + `shellcheck`, and the build/sign/verify commands run locally.

## Rollback

State machine (`ota_usb.cpp`, tested on the host with a fake flash: `firmware/host_test/ota_host.cpp`):

1. OTA_END succeeds: the NVS flag `inkota/usbpend` is set, *then* the boot partition is flipped
   (so power loss in between leaves a stale flag that the next boot clears), then ACK, then reboot.
2. Bootloader boots the new image in state `PENDING_VERIFY`. `Receiver::begin()` sees
   pending + flag, and arms a 180 s `esp_timer` (it runs even if `setup()` hangs).
3. Host `confirm_boot`: HELLO request, reply must carry the expected `fw_ver` and protocol,
   then it sends the HELLO ack. The panel compares `proto_ver` and `fw_ver` with what it reports
   and calls `esp_ota_mark_app_valid_cancel_rollback()`, disarms the timer, clears the flag.
4. No ack (host absent, mismatch, firmware hung): the timer calls
   `esp_ota_mark_app_invalid_rollback_and_reboot()`. A crash or reset loop before that is
   handled by the bootloader itself (a reset while pending counts as failed).
5. Images that were not installed over USB (the old Wi-Fi web/HTTP update) have no flag and
   are marked valid immediately at boot, as before.

Consequence worth knowing: if the user unplugs the panel in the 180 s after an update and
never reconnects, the panel rolls back to the previous firmware and the plugin offers the update
again next time.

**Build configuration.** No special `platformio.ini` settings are needed. The prebuilt
Arduino-ESP32 2.0.17 libs for esp32s3 (`framework-arduinoespressif32/tools/sdk/esp32s3/sdkconfig`)
already have `CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y` (bootloader built with rollback, it is
what `pio ... upload` flashes) and `CONFIG_APP_ROLLBACK_ENABLE=y`. What does matter: Arduino's
`initArduino()` marks any pending image valid before `setup()` unless the weak hook
`verifyRollbackLater()` returns true. `ota_usb.cpp` overrides it (`extern "C"`) and
`Receiver::begin()` decides instead. `ota_usb.cpp` has a compile-time `#error` if either
`CONFIG_*ROLLBACK_ENABLE` ever disappears (e.g. an unpinned `platform = espressif32` upgrade).
`esp_ota_begin(..., OTA_WITH_SEQUENTIAL_WRITES)` makes the erase incremental, so OTA_BEGIN
returns instantly and the USB link is never starved by a whole-slot erase. `firmware/partitions.csv`
(nvs, otadata, two 0x330000 app slots) fits the image (currently 1.48 MB).

## Tests

```bash
cd decky && python3 -m pytest -q tests                   # everything, including the C++ parts
cd decky && python3 -m coverage run --branch --source=py_modules/inkterface_core/ota -m pytest -q tests \
    && python3 -m coverage report -m                     # needs: pip install coverage
cd firmware && g++ -std=c++17 -O1 -Wall -Wextra -fsanitize=address,undefined -Isrc/ee05 \
    host_test/ota_host.cpp src/ee05/ota_usb.cpp src/ee05/usbproto.cpp -o /tmp/ota_host && /tmp/ota_host --selftest
```

| area | file |
|---|---|
| ed25519: RFC 8032 vectors, every single-bit flip, wrong key, malleable S, small-order keys, `cryptography` cross-check | `decky/tests/ota/test_ota_ed25519.py` |
| manifest schema, semver order, signature, downgrade / protocol / min plugin | `.../test_ota_manifest.py` |
| GitHub: listing, prerelease/draft, 404, 5xx, rate limit, malformed JSON, missing assets, URL allowlist, download cap, hash/size mismatch, urllib wrapper | `.../test_ota_github.py` |
| Rate limit: ETag / 304 cache (stored, sent, corrupt, bounded), Retry-After / reset, cached and github.com fallbacks (signature still gating) | `.../test_ota_ratelimit.py`, `decky/tests/test_ota_plugin.py` |
| streamer vs emulator: success, NAK mid-stream, link drop + resume, cancel + resume, lost frame, timeouts, retries, confirm_boot, rollback | `.../test_ota_stream.py` |
| key + signing tools, `verify_release`, whole chain sign to flashed emulator | `.../test_ota_tools.py` |
| firmware state machine vs emulator, byte for byte (scripted + 40 random streams), and the C++ selftest: out-of-order, overflow, bad hash, bad image, flash faults, power loss mid-write (old image intact), resume, mark-valid, verify-timer rollback, legacy image, millis wrap | `decky/tests/test_ota_cpp.py`, `firmware/host_test/ota_host.cpp` |

## Real hardware (not covered by the automated tests)

Verified on a Steam Machine by the maintainer:

- 2026-11-12: a real signed GitHub release, `fw-v1.0.0-beta.2` (a pre-release), was found, verified and installed end to end by the
  plugin, updating the panel from beta.1 to 1.0.0-beta.2. The new image was accepted and there was no rollback. Until then
  the update path had only run against a fake GitHub with a test key.
- 2026-11-10: a forced rollback recovered (criterion 8 of the v1 hardware checks). A test-key OTA image that was never
  confirmed was sent, and the panel rolled back to the old firmware 181.2 s after the image was sent.
- 2026-11-12: the panel re-enumerated after the reboot and the host's HELLO ack was accepted (the install is only confirmed after it).

Not recorded anywhere yet:

- The real transfer time and the actual flash behaviour of `esp_ota_begin(SEQUENTIAL_WRITES)` / `esp_ota_end`. Estimate
  only: ~1.5 MB over a ~234 KB/s link is ~6 s of wire time, plus 4 KB sector erases while writing, so likely 15-30 s total.
- Unplugging the cable during OTA_DATA and checking that the old firmware boots.
- HWCDC behaviour when the panel is busy writing flash: no bytes lost with the 8192-byte window? (`OtaResync` recovers if
  some are.)
- The measured time from reboot to HELLO and the device path after re-enumeration (the Wi-Fi connect in `setup()` can
  delay it by ~25 s, well inside the 180 s verify window and the 120 s `confirm_boot` default).
