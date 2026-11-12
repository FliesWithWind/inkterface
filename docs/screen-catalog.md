# Screen catalog: community screens in a signed store

Users install community screens from the **Screen Store** page of the plugin. The screens live in a separate repository,
`github.com/FliesWithWind/inkterface-screens` (the "catalog repo"). People commit screens there by pull request; CI validates
them; every merge to `main` publishes a **signed index** that the plugin verifies before it shows or installs anything.

Importing a zip or an https link still exists, but only as a **developer** feature (Settings, Developer mode, off by default).

```
contributor PR ──> catalog repo CI (validate, render preview) ──> merge to main
                                                                     │
                     build zips + previews + index.json, sign index.json (INKTERFACE_CATALOG_KEY)
                                                                     │
                                   GitHub Pages:  <base>/index.json  index.json.sig  zips/  previews/
                                                                     │
Steam Machine: plugin Screen Store page ── catalog_refresh() ── verify signature with the bundled catalog key
                                 ── catalog_install()  ── sha256 from the signed index ── PackageStore (all import checks)
```

Code: plugin `decky/py_modules/inkterface_core/catalog/` (`trust.py`, `index.py`, `client.py`) and `PluginCore.catalog_*`; frontend
`decky/src/pages/StorePage.tsx`. Catalog repo: `tools/` (validate, render_preview, build_catalog, sign_index, gen_keypair), `.github/workflows/`.

## Published files

Base URL (default, changeable in Settings, https only): `https://flieswithwind.github.io/inkterface-screens/`

| File | What |
|------|------|
| `index.json` | canonical JSON (`indent=1`, sorted keys, UTF-8, trailing newline) |
| `index.json.sig` | raw 64-byte ed25519 signature over the **exact bytes** of `index.json` |
| `zips/<id>-<version>.zip` | the package, reproducible build (fixed timestamps), without `README.md` and `preview.png` |
| `previews/<id>-<version>.png` | 648x480 1-bit preview, freshly rendered by CI with the plugin's own renderer |

```json
{"schema": 1, "serial": 1731326400, "generated": "2026-11-11T12:00:00Z",
 "screens": [{"id": "gpu-focus", "name": "GPU Focus", "version": "1.0.0", "author": "inkterface contributors",
   "description": "...", "has_code": false, "has_active": true, "has_idle": false,
   "zip": "zips/gpu-focus-1.0.0.zip", "sha256": "<64 hex of the zip>", "size": 4321,
   "preview": "previews/gpu-focus-1.0.0.png", "preview_sha256": "<64 hex>", "min_plugin_version": "1.0.0"}]}
```

One version per screen (the newest). Unknown keys are rejected. `has_code`, `has_active`, `has_idle` are **derived by CI from the validated
package**, never taken from what a contributor writes. Paths are relative and must match `zips|previews/<id>-<version>.zip|png` for the entry's
own id and version: the index cannot send the plugin to another host.

## Trust model

It has the same shape as the firmware updates ([ota.md](ota.md)), with its own key.

| Rule | Where |
|------|-------|
| One **catalog** ed25519 key, separate from the firmware key (a leaked catalog key cannot sign firmware, and the reverse) | `catalog/trust.py` `PRODUCTION_PUBLIC_KEY_HEX`, private half = Actions secret `INKTERFACE_CATALOG_KEY` |
| **Fail closed**: a build with no key (`PRODUCTION_PUBLIC_KEY_HEX` empty, or a test build that injects `None`) says "no catalog signing key is bundled" and refresh, list and install all refuse. The shipped value is the project's catalog public key | `Catalog._require_key` |
| Signature is checked **before** the index is parsed; the cache is re-verified on every read, so an edited cache file is refused | `catalog/index.py`, `Catalog._read_cache` |
| The only key a build trusts is the bundled one. The TEST-ONLY key reaches the client through the `catalog_key` constructor argument (`main.catalog_kw`), never an environment variable, setting or callable | `decky/tests/test_catalog_testkey_not_shipped.py` |
| **Rollback**: an index with a lower `serial` than the saved one is refused | `Catalog.refresh` |
| **Integrity**: the zip must match `size` and `sha256` of the signed entry; the preview must match `preview_sha256` and be a 648x480 PNG | `Catalog.fetch_zip`, `preview_png` |
| **Downgrade**: an install must be strictly newer (semver) than what is installed; `version` must equal the catalog's version | `PackageStore`, `PluginCore.catalog_install` |
| The signed claims are checked against the real package: id, version and `has_code` of the zip must equal the entry, else nothing is installed | `PackageStore.install_bytes(expect=...)` |
| **All import checks still apply**: the bytes go through the same `load_zip` (zip limits, names, symlinks, `screen.json` validator, one `screen.py`) as a developer import | `docs/screen-packages.md` |
| Code screens still install **disabled** and need the approval dialog (hash of `screen.py`). The catalog does not change what a code screen is allowed to do | `PackageStore` |
| `min_plugin_version` newer than the plugin refuses the install | `catalog_install` |

What the signature proves: the index and (through its hashes) every zip and preview were produced by the CI of the catalog repo with the key in
the repo's secret. It does **not** prove a screen is safe: for a code screen the protection is review in the catalog repo (every `screen.py`
change needs a code-owner review) plus the user's approval of one hash. A code screen is guarded, not sandboxed
([screen-packages.md](screen-packages.md#security-model-and-its-limits)).

Known limits: there is no index expiry (an attacker who can block the network can keep a user on an old, still validly signed catalog; the serial
rule only stops a *served* rollback after a newer index was seen). A custom catalog URL can only serve content signed by the bundled key, so it is
a mirror, not a way to add a catalog. A screen installed from the catalog and one imported by a developer with the same id are the same thing to the
plugin (no origin is recorded): an update replaces either, and a code screen needs approval again.

## Plugin callables

All return `{ok: true, ...}` or `{ok: false, error}` and never raise. Full table in [plugin-ui.md](plugin-ui.md#callables).

| Callable | Does |
|----------|------|
| `catalog_refresh()` | fetch `index.json` + `.sig`, verify, rollback check, save to `<settings dir>/catalog/`. Network or trust failure leaves the cache as it was |
| `catalog_list()` | the saved catalog (no network) with `installed`, `installed_version`, `update_available`, `needs_approval`, `compatible` per entry |
| `catalog_updates()` | installed screens that have a newer catalog version |
| `catalog_install(screen_id, version)` | download, check size and sha256, install through `PackageStore`; `{package, needs_approval}` like `import_package` |
| `catalog_preview(screen_id)` | the preview as a data URL, cached on disk by id and version, works offline |

Settings: `catalog_url` ("" = default, https only, a missing trailing `/` is added) and `developer_mode` (default off).
`import_package` fails while Developer mode is off with: `Importing a zip or link is a developer feature. Turn on Developer mode in Settings first.`

## Why GitHub Pages

The catalog is published to GitHub Pages (`actions/deploy-pages`) and not as a release asset:

- one stable base URL for the index, the zips and the previews, so the plugin builds every URL from one setting;
- a CDN with no API rate limit (release assets are reached through the GitHub API or per-tag URLs; the firmware updater already has to handle
  rate limits for exactly that reason);
- an atomic deploy: the site is replaced as a whole, so a client never sees a new index next to old zips;
- nothing to delete or re-upload per version, and the files are plain static files that are easy to mirror.

The cost is that Pages needs the repo to be public and Pages enabled; the maintainer setup below covers it.

## Maintainer setup (once)

The plugin bundles the catalog public key (`catalog/trust.py`) and the default catalog URL (`catalog/client.py` `DEFAULT_URL`). The catalog CI is
self-contained: the catalog repo holds a pinned copy of the plugin's validator, renderer, package loader and ed25519 code under `vendor/inkterface_core/`,
so its workflows never need the plugin repo. `vendor/PLUGIN_COMMIT` names the plugin commit the copy came from, and the repo's `check_vendor` tool runs
first in both workflows (every file must match `vendor/SHA256SUMS`). To refresh the copy after a format change, run
`python3 tools/sync_vendor.py --plugin <plugin checkout> --commit <sha>` and then `python3 tools/check_vendor.py` in the catalog repo.

The GitHub side needs the GitHub CLI logged in with admin rights on the catalog repo (`gh auth status`). Nothing here is automated and no secret
goes into either repo. `<owner>` is the GitHub account or organisation that holds the catalog repo.

1. **Generate the catalog key** (once). Back the private file up offline; losing it means a new plugin release with a new public key.

   ```sh
   python3 tools/release/gen_keypair.py --private <path-to-catalog-key> --public <path-to-catalog-pub>
   ```

   Paste the public hex into `PRODUCTION_PUBLIC_KEY_HEX` in `decky/py_modules/inkterface_core/catalog/trust.py` and commit the same value as
   `catalog-pubkey.txt` in the catalog repo.

2. **Create the public catalog repo and push.** The first push to `main` starts the `Publish catalog` workflow, which fails on purpose until the
   secret exists (it refuses to publish an unsigned index).

   ```sh
   git status --short                # must print nothing
   gh repo create <owner>/inkterface-screens --public --source . --remote origin
   git push -u origin main
   git ls-files | grep -iE '\.key$'  # must print nothing: no secret in the repo
   ```

3. **Set the signing secret** (value read from the file, never shown).

   ```sh
   gh secret set INKTERFACE_CATALOG_KEY --repo <owner>/inkterface-screens < <path-to-catalog-key>
   ```

   `publish.yml` signs with this secret and, because `catalog-pubkey.txt` is committed, fails if the secret does not belong to that public key.
   A wrong or empty secret can never publish an index the plugin would reject.

4. **Enable GitHub Pages** with the source "GitHub Actions". A `409` answer means the site exists already.

   ```sh
   gh api -X POST repos/<owner>/inkterface-screens/pages -f build_type=workflow
   gh api repos/<owner>/inkterface-screens/pages --jq '{build_type, html_url}'
   ```

   `html_url` must equal the plugin's `DEFAULT_URL`.

5. **Protect `main`**: pull request required, code-owner review required (`CODEOWNERS` makes every `screen.py` change need a code owner), and the
   `validate` check (job of `PR validation`) must pass. `enforce_admins` is false so that a sole maintainer can still merge their own pull requests.

   ```sh
   gh api -X PUT repos/<owner>/inkterface-screens/branches/main/protection --input - <<'EOF'
   {
     "required_status_checks": {"strict": true, "contexts": ["validate"]},
     "enforce_admins": false,
     "required_pull_request_reviews": {"require_code_owner_reviews": true, "required_approving_review_count": 1, "dismiss_stale_reviews": true},
     "restrictions": null,
     "allow_force_pushes": false,
     "allow_deletions": false
   }
   EOF
   ```

6. **First publish and check.** Run the workflow, then verify the signature of what was published against the bundled key:

   ```sh
   gh workflow run publish.yml --repo <owner>/inkterface-screens
   curl -fsS -o index.json     <catalog-url>/index.json
   curl -fsS -o index.json.sig <catalog-url>/index.json.sig
   python3 - <<'EOF'
   import sys
   sys.path.insert(0, "decky/py_modules")
   from pathlib import Path
   from inkterface_core.ota import ed25519
   pub = bytes.fromhex(Path("<path-to-catalog-pub>").read_text().strip())
   ok = ed25519.verify(pub, Path("index.json").read_bytes(), Path("index.json.sig").read_bytes())
   print("SIGNATURE OK" if ok else "SIGNATURE BAD")
   EOF
   ```

   If the plugin's Refresh says "catalog index signature does not match the bundled catalog key", the plugin and the published index use different
   keys: compare `catalog-pubkey.txt` with `PRODUCTION_PUBLIC_KEY_HEX`.

Key rotation: generate a new pair, ship a plugin with the new `PRODUCTION_PUBLIC_KEY_HEX`, replace `catalog-pubkey.txt`, then `gh secret set` the new key.
Plugins with the old key show the signature error until updated (intended). Changing the catalog URL: edit `DEFAULT_URL`; users can also override it in
Settings (https only), and a mirror can only serve content signed with the bundled key.
If the secret is empty or belongs to another key, the publish job fails and nothing is deployed; it never publishes an unsigned index.

## Contributor and reviewer rules (catalog repo)

- Every screen is contributed under **MIT** (PR template checkbox; the repo is MIT).
- Declarative screens: normal review. **Code screens: extra review**: a maintainer reads every line of `screen.py`; the README must have a
  `## What the code does` section; CI prints the sha256 of `screen.py` so the reviewer can compare it with the plugin's approval dialog.
- CI (`pr.yml`) runs on `pull_request` with no secrets: it validates with the plugin's own validator, checks id == folder name, no built-in
  id, size limits, `screen.py` present exactly when `screen.json` declares it, the committed `preview.png` against a fresh render (1 % tolerance),
  and a version bump when any file that lands in the zip changed. Note it runs a code screen's `screen.py` in the plugin's guarded runner on the CI
  machine, with no secrets and a read-only token; the signing key exists only in the `publish` workflow on `main`.
- Details and commands: the catalog repo's README and CONTRIBUTING.

## Tests

Plugin: `decky/tests/catalog/` (index schema, signature good/bad/wrong key/empty key, sha256 and size mismatch, downgrade and rollback, offline
cache, install reusing the package checks, URL validation, developer-mode gate), `decky/tests/test_catalog_testkey_not_shipped.py`,
and `decky/test/StorePage.test.tsx`, `SettingsPage.test.tsx`, `PackagesPage.test.tsx` (vitest). Catalog repo: `tests/` (validator, builder, signing,
key generation, seed screens). End to end (index built from the catalog repo, signed with a throwaway key, served on 127.0.0.1, installed through
`PluginCore`): `decky/tests/plugin/test_catalog_e2e_repo.py`, runs when the catalog repo is checked out next to this one (`INKTERFACE_CATALOG_REPO`).
