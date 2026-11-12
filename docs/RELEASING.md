# Releasing (current release: 1.0.0-beta.2)

Release steps for maintainers. Background and trust model: [ota.md](ota.md). Version rules: [../CONTRIBUTING.md](../CONTRIBUTING.md#releases).

Two tags make two GitHub releases, both from the same commit on `main`:

| Tag | Workflow | Release assets |
|-----|----------|----------------|
| `fw-v1.0.0-beta.2` | `.github/workflows/firmware-release.yml` | `inkterface-ee05-1.0.0-beta.2.bin`, `manifest.json`, `manifest.sig` |
| `plugin-v1.0.0-beta.2` | `.github/workflows/plugin-release.yml` | `inkterface-plugin-1.0.0-beta.2.zip` |

Both are marked **pre-release** by the workflows because the version has a `-` suffix. A pre-release plugin also offers
pre-release firmware, so beta users still get firmware updates.

You need: `git`, `gh` (logged in with write access to the repo), Python 3.14, and the repo checked out on the release
branch. The commands below assume the GitHub remote is called `origin` (`git@github.com:FliesWithWind/inkterface.git`).

## 1. Create the signing keypair (once, on your own machine)

```sh
python3 tools/release/gen_keypair.py --private <path-to-private-key> --public <path-to-public-key>
```

It prints `PRODUCTION_PUBLIC_KEY_HEX = "<64 hex>"`. The private file is written with mode 0600 and the command refuses to
overwrite an existing file.

* Back the private file up offline (password manager or encrypted drive). It is not in the repo and nothing else has a copy.
  Lose it and the next plugin release has to ship a new public key; panels already in the field keep working.
* Never commit it, never paste it into an issue.

## 2. Put the private key in the repository secret

```sh
gh secret set INKTERFACE_SIGNING_KEY --repo FliesWithWind/inkterface < <path-to-private-key>
gh secret list --repo FliesWithWind/inkterface      # INKTERFACE_SIGNING_KEY is listed
```

CI reads it only to sign `manifest.json` of a tagged firmware release.

## 3. Paste the public key and commit it on the release branch

Edit `decky/py_modules/inkterface_core/ota/trust.py`:

```python
PRODUCTION_PUBLIC_KEY_HEX = "<the 64 hex chars from step 1>"
```

Then check and commit (the key must be in the commit you tag, because `firmware-release.yml` verifies the signed release
against it before publishing; with the key still empty that step fails and nothing is published):

```sh
decky/scripts/vendor.sh
python3 -m pytest decky/tests -q
git add decky/py_modules/inkterface_core/ota/trust.py
git commit -m "ota: bundle the production release public key"
```

## 4. Push the branch, open the PR, merge

`decky/py_modules/PIL`, `pillow*`, `certifi` and `certifi-*` are not tracked: `decky/scripts/vendor.sh` fetches them and checks
their sha256. If an older checkout still has them tracked, git removes them on checkout; run `decky/scripts/vendor.sh` to get them back.

```sh
git push -u origin <release-branch>
gh pr create --repo FliesWithWind/inkterface --base main --head <release-branch> \
  --title "Release 1.0.0-beta.2" --body "See CHANGELOG.md and docs/RELEASING.md"
```

`ci.yml` runs on the PR: pytest (with the OTA coverage gate), the `ee05` firmware build with the host tests, and the
frontend (typecheck, vitest, build, zip). Merge when green. Then:

```sh
git checkout main && git pull origin main
git log -1            # this is the commit that will be tagged
```

## 5. Tag and push

`VERSION` and `decky/package.json` already say `1.0.0-beta.2`; both workflows refuse a tag that disagrees.

```sh
git tag fw-v1.0.0-beta.2
git tag plugin-v1.0.0-beta.2
git push origin fw-v1.0.0-beta.2 plugin-v1.0.0-beta.2
```

## 6. What CI produces

`firmware-release`: runs the full pytest suite, builds `ee05` with `INKTERFACE_FW_VERSION=1.0.0-beta.2`
(the string the panel reports in HELLO, and the signed manifest version), checks that the version string is in the binary,
writes a changelog from `git log`, signs `manifest.json` with `INKTERFACE_SIGNING_KEY`, runs `tools/release/verify_release.py`
with the key bundled in `trust.py` (a wrong or stale secret fails here, before anything is published), and publishes the
three assets.

`plugin-release`: checks the tag against `VERSION` and `package.json`, runs pytest, typecheck, vitest and the pnpm build,
fetches the pinned Pillow (`decky/scripts/vendor.sh`), builds the static idle-preview renderer
(`decky/scripts/build_idle_render.sh`, output is gitignored and shipped in the zip as `bin/idle_render_host`;
`make_zip.py` refuses to build without it), builds the zip with `decky/scripts/make_zip.py`, checks its layout and
publishes `inkterface-plugin-1.0.0-beta.2.zip`.

Watch them with `gh run list --repo FliesWithWind/inkterface` and `gh run watch`. A failed run publishes nothing; fix, delete
the tag locally and on the remote (`git push origin :refs/tags/<tag>`), and tag again.

## 7. Verify the signed firmware release independently

Do this from a machine that has the private key's **public** half from step 1 (`<path-to-public-key>`), so you are not
trusting the repo to tell you which key is right:

```sh
mkdir /tmp/fwrel && cd /tmp/fwrel
gh release download fw-v1.0.0-beta.2 --repo FliesWithWind/inkterface
cd -
python3 tools/release/verify_release.py /tmp/fwrel --pubkey "$(cat <path-to-public-key>)"
# OK: inkterface-ee05-1.0.0-beta.2.bin v1.0.0-beta.2 protocol 1 <size> bytes sha256 ...
python3 tools/release/verify_release.py /tmp/fwrel     # same check with the key bundled in trust.py
```

`REJECTED: ...` means the plugin would refuse this release too. The check covers the Ed25519 signature over `manifest.json`,
the manifest schema, and the `.bin` size and sha256.

## 8. Install on the Steam Machine and check

1. Download `inkterface-plugin-1.0.0-beta.2.zip` from the release. In Decky, enable **Developer mode**, then **Install plugin
   from ZIP file** (this needs root on the Steam Machine).
2. Flash a new panel once over USB with the tagged firmware, so it reports the release version
   (the readme has the backup and restore steps):

   ```sh
   git checkout fw-v1.0.0-beta.2
   cp -n firmware/src/ee05/idle_config.h.example firmware/src/ee05/idle_config.h
   cd firmware && INKTERFACE_FW_VERSION=1.0.0-beta.2 pio run -e ee05 -t upload --upload-port <PORT>
   ```

3. Open Inkterface in the quick-access panel: `Connected - firmware 1.0.0-beta.2`, the Firmware page shows no "No signing key"
   notice and says it is up to date.
4. Install the plugin in Decky on a Steam Machine and confirm the pages work in Gaming Mode.
5. The first real-release OTA is tested by publishing a newer one: change `VERSION`, `decky/package.json` and `CHANGELOG.md`
   to `1.0.0-beta.2`, release, and install it from the Firmware page.

## Pre-tag checklist

* `decky/plugin.json` has `"flags": []`: the template's `"debug"` flag is gone (a test checks the release zip's `plugin.json`, and
  the plugin workflow checks it again). In Decky 3.3.1 the flag only turns on hot reload of a changed `dist/index.js` or `main.py`;
  developers add it to the installed copy, see [../decky/README.md](../decky/README.md#hot-reload-while-developing).
* Turn on **private vulnerability reporting** (repository Settings, Code security) so [../SECURITY.md](../SECURITY.md) works.
* `decky/LICENSE` is the project's MIT text (same as [../LICENSE](../LICENSE), a test keeps them equal) and `decky/package.json`
  says `MIT`. The Decky template's BSD-3-Clause notice for the template files that remain (`rollup.config.js`, `tsconfig.json`,
  the build scripts) is in [../THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).
* The 4-gray waveform is not third-party data: it is the panel maker's OTP waveform, selected by two register writes
  ([gray-waveform.md](gray-waveform.md)). Before tagging, look at a gray4 idle screen on the panel once:
  four distinct grays, no smear, no leftover after switching back to mono.
* `publish.image` in `plugin.json` is the emulator-rendered Clock & Weather screen
  (`decky/tests/idle/goldens/clock-weather.png`, via raw.githubusercontent.com on `main`). It only resolves once the file is on `main`;
  a test keeps it pointing at this repo and at a file that exists.
* `firmware/platformio.ini` pins `espressif32@7.1.3`, `ArduinoJson@7.4.3` and, through `platform_packages` for `ee05` and `ee05_pbench`,
  Arduino-ESP32 `4.20017.260907+sha.dcc1105b` and `tool-esptoolpy 2.41100.260830`. The toolchains are exact in the platform itself; the
  unused filesystem tools (`tool-mkfatfs`, `-mklittlefs`, `-mkspiffs`) still float and do not touch the image. When you bump a pin,
  rebuild, compare the image size and update THIRD_PARTY_NOTICES.
