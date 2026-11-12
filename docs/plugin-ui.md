# Decky plugin: backend callables and UI

How the plugin is wired and what each part of the UI does.
Engine: [engine.md](engine.md). Packages: [screen-packages.md](screen-packages.md). Screen Store: [screen-catalog.md](screen-catalog.md). Sources: [data-sources.md](data-sources.md).

```
Steam UI (React, decky/src)  <--callable / events-->  main.py (thin async shell)
                                                         |  asyncio.to_thread
                                                  inkterface_core/plugin_core.py  PluginCore  (no decky imports)
                                          Registry (data) + Engine (panel loop) + PackageStore (screens)
```

`main.py` only adapts Decky: it builds `PluginCore(DECKY_PLUGIN_SETTINGS_DIR, DECKY_PLUGIN_DIR/screens)`, runs every core
call in a worker thread (a slow `screen.py` or the engine lock never stalls Decky's event loop), emits events, and routes
`inkterface_core` logs into `decky.logger`. All behaviour is in `PluginCore`, which tests drive without Decky.

## Lifecycle

| Hook | What happens |
|------|--------------|
| `_main` | attach logging, `PluginCore.start()`: load `settings.json` + `host.json` (bad files fall back to defaults, are moved to `*.corrupt`, and the reason shows in the UI), open the `PackageStore`, start the data sources, start the engine thread (it connects to the panel by USB VID 303a and keeps reconnecting), start the 1 s status watcher. If any step fails everything already started is stopped again and the error propagates (Decky shows the plugin as failed). |
| `_unload` | cancel the watcher, `PluginCore.shutdown()`: `engine.stop()` sends `MODE PANEL` (the panel shows its own screen at once) and closes the port, then the source threads are joined. No thread or task is left. Safe to call twice or without `_main`. |
| `_uninstall` | **keeps** `settings.json`, `host.json` and `screens/` (installed packages, approvals) **unless** the user switched on *Delete my settings and packages when the plugin is uninstalled* (`purge_on_uninstall`), in which case only those files are removed. |

Files in `DECKY_PLUGIN_SETTINGS_DIR`: `settings.json` (engine settings), `host.json` (weather, Steam credentials, idle selection; mode 0600),
`screens/` (package store, see screen-packages.md). Built-in screens ship in the plugin's `screens/`.

## Conventions

- Reads (`get_status`, `list_screens`, `get_settings`) return the data. If the plugin is not running they **reject** (`Inkterface is not running`); the UI shows that as an error state.
- Every other callable **never raises for bad input**: it returns `{"ok": true, ...}` or `{"ok": false, "error": "<why>"}`. The frontend wrapper (`src/api.ts`) also turns a rejected call into `{ok: false, error}`.
- Validation is all-or-nothing: an invalid value changes nothing and nothing is saved.
- Screen ids are `[a-z0-9-]`; app ids are positive integers.

## Callables

| Callable | Arguments | Returns |
|----------|-----------|---------|
| `get_status` | none | engine status (see [engine.md](engine.md#status)) plus `ui_state` (`connected` / `connecting` / `waiting` / `firmware_too_old` / `updating` / `stopped`), `idle_supported`, `idle` (see [Idle layouts on the panel](#idle-layouts-on-the-panel)), `plugin_version`, `host_error`. `last_error` is scrubbed of the Steam key. |
| `list_screens` | none | list of screens (built-in and installed): `id, name, version, author, description, builtin, enabled, has_code, code_sha256, approved, needs_approval, has_active, has_idle, refresh_s, settings_schema, settings` (effective values), `current, pinned, in_rotation, in_idle`, for screens with an idle layout `idle_modes` (render modes it compiles in) and `idle_mode` (the one that would be uploaded), and `error` if the screen or its stored settings are broken. |
| `get_settings` | none | engine settings + `weather_lat, weather_lon, units, steamid64, idle_screens, idle_modes, idle_rotate_s, wifi_ssid, purge_on_uninstall`, `steam_api_key` and `wifi_pass` as **`"set"` or `"unset"`**, and `errors` (problems found when the files were loaded). |
| `set_settings` | `patch` object | `{ok, settings}`. Allowed keys: `rotation_interval_s` (5..86400), `idle_timeout_s` (15..3600, sent to the panel in `MODE`), `ghost_clear_minutes` (0..120, 0 = never; minutes between the full refreshes that clear ghosting, see [ghost-clear.md](ghost-clear.md)), `auto_now_playing`, `now_playing_screen`, `ingame_enabled` (bool, default false; the first time it is turned on the in-game profile starts as a copy of the normal one), `weather_lat` (-90..90) with `weather_lon` (-180..180) (both or neither; `null` clears), `weather_place` (text, at most 120 characters: the name of the place the coordinates were picked for; moving the coordinates without sending a new `weather_place` clears it), `units` (`metric`/`imperial`), `steamid64` (empty or 17 digits), `steam_api_key` (empty to remove, else 32 hex characters), `idle_screens` (installed screens that have an idle layout, in display order), `idle_modes` (`{screen id: mono|gray4}`, only modes the layout compiles in), `idle_rotate_s` (0 = stay on the first, else 10..65535), `wifi_ssid` (<= 32 bytes), `wifi_pass` (<= 63 bytes, empty to remove), `purge_on_uninstall`, `developer_mode` (bool, default false), `catalog_url` (empty = default, else an `https://` URL up to 300 characters without spaces or user info; a trailing `/` is added). Changing any idle-related key rebuilds the idle store; it is uploaded if the bytes differ. Changing a source setting rebuilds the data registry. `rotation`, `pinned` and per-screen values have their own callables. |
| `set_screen_settings` | `screen_id, values` | validated against the screen's `settings` schema (type, min/max, enum). `{}` resets the screen to its defaults. `{ok, settings}` with the effective values. |
| `pin_screen` / `unpin_screen` | `screen_id, profile?` / `profile?` | pin needs an enabled screen with an `active` section. A pin beats now-playing and rotation. `profile`: omitted or `"normal"` (not in a game), or `"ingame"`. |
| `set_rotation` | `screen_ids, interval_s?, profile?` | non-empty list of enabled active screens; optional new interval; `profile` as for `pin_screen`. |
| `set_screen_enabled` | `screen_id, enabled` | enabling a code package that is not approved is refused. Disabling the pinned screen unpins it. |
| `set_now_playing` | `appid` or `null` | called by the frontend from SteamClient events. Validated here (positive integer, digits-only string ok). Works with no panel connected. |
| `import_package` | `source` (file path or `https://` URL) | **Developer feature**: refused with `Importing a zip or link is a developer feature. Turn on Developer mode in Settings first.` unless `developer_mode` is on. `{ok, package, needs_approval}`. All checks of [screen-packages.md](screen-packages.md) apply; nothing is installed unless the whole package is valid. A code package is installed **disabled**. |
| `catalog_refresh` | none | fetch the signed catalog index and its signature, verify with the bundled catalog key, refuse a lower `serial`, save to the cache. `{ok, fetched_at, count}`. Offline or a bad signature: `{ok: false, error}` and the saved catalog stays usable. With no bundled key (the production value is empty until a maintainer sets it) every catalog call refuses. See [screen-catalog.md](screen-catalog.md). |
| `catalog_list` | none | the saved catalog, no network: `{ok, entries, fetched_at, url}`; per entry `id, name, version, author, description, has_code, has_active, has_idle, size, min_plugin_version, installed, installed_version, update_available, needs_approval, compatible, incompatible_reason`. Nothing saved yet: `entries: []`, `fetched_at: null`. A saved catalog that fails verification: `{ok: false, error}`. |
| `catalog_updates` | none | `{ok, updates: [{id, name, installed_version, version}]}` for installed (non built-in) screens with a newer catalog version. |
| `catalog_install` | `screen_id, version` | `version` must be the catalog's. Downloads the zip, checks size and sha256 against the signed index, then installs through the same `PackageStore` path as `import_package` (all package checks; id, version and `has_code` must equal the signed entry; strictly newer than any installed version). `{ok, package, needs_approval}`; a code screen is installed **disabled** and needs `approve_package`. Works without Developer mode. |
| `catalog_preview` | `screen_id` | `{ok, data_url}`: the signed preview (sha256 checked, 648x480 PNG), cached on disk, works offline once cached. |
| `search_places` | `query`, `language?` | `{ok, results: [{label, name, lat, lon, country_code}]}`: [Open-Meteo geocoding](https://open-meteo.com/en/docs/geocoding-api) (no key, https, through the shared TLS context), up to 8 places, best first. `label` ("Kraków, Lesser Poland, Poland") tells equal names apart, `name` ("Kraków, Poland") is what Settings shows. The query is 2..80 characters without control characters, URL-encoded; `language` must be 2 or 3 lowercase letters, else `en`. `{ok: false, error}` for a bad query, no match, or a network error (HTTP status or the transport reason, never the URL). |
| `locate_by_ip` | none | `{ok, name, lat, lon, country_code, provider: "ipwho.is"}`: approximate position of this connection's public IP, rounded to 2 decimals, from [ipwho.is](https://ipwho.is/) (no key). Only called when the user presses **Use approximate location**; nothing is saved by the call. |
| `resolve_steam_id` | `text` | `{ok, steamid64}` from what the user pasted: a SteamID64, a `steamcommunity.com/profiles/<id>` link, SteamID3 `[U:1:n]`, SteamID2 `STEAM_0:y:n`, a 32-bit account number (all pure), or a custom name / `steamcommunity.com/id/<name>` link, looked up with `ISteamUser/ResolveVanityURL` and the **saved** key (without a key: an explanation). Only `steamcommunity.com` links are accepted. A 17-digit value must be a public-universe individual account. |
| `steam_profile` | `steamid64?` (default: the saved one) | `{ok, steamid64, persona, avatar, public}`: persona name and avatar (a data URL, only fetched from Steam's own image hosts over https, at most 128 KB, magic bytes checked; `null` when it cannot be had) for the Settings page. With the saved key it uses `GetPlayerSummaries`, without one the public profile's XML page. |
| `test_steam_key` | none | `{ok, persona, profile_public, games_visible}` or `{ok: false, error}`. Calls `GetPlayerSummaries` for the **saved** SteamID64 with the **saved** key (the key never comes from or goes to the UI), then `GetOwnedGames` to see whether *Game details* is public (`games_visible: null` when that second call fails: not an error). Errors: no key saved, no SteamID saved, `Steam rejected this key` (HTTP 401/403), unknown account, `Cannot reach Steam: ...`, rate limit (429), Steam down (5xx). Every message is scrubbed of the key; the key and URL are never logged. |
| `wifi_current` | none | `{ok, ssid, saved}`: the Wi-Fi network the Steam Machine is on (`nmcli`, no sudo, no shell, 5 s timeout, `LD_LIBRARY_PATH` restored to the original so Decky's frozen process does not break it). On a cable `ssid` is `null` and `saved` lists up to 8 remembered Wi-Fi networks. The password cannot be read without root, so none is ever returned. Errors: no `nmcli`, NetworkManager not answering, nothing to copy. |
| `approve_package` | `screen_id, version, sha256` | must equal what is installed **now** (id, version, sha256 of `screen.py`), else refused with a message saying to review again. Enables the screen. |
| `uninstall_package` | `screen_id` | built-ins cannot be uninstalled. Also removes the id from the rotation, the pin, per-screen settings and the idle selection. |
| `preview_screen` | `screen_id` | `{ok, data_url}` : a 648x480 1-bit PNG data URL, rendered with the live data and the user's settings by the same renderer the panel uses (no panel needed). Declarative screens preview even when disabled. A code screen previews only when approved and enabled (unapproved code is never run). |
| `preview_idle_screen` | `screen_id, mode?` (`"mono"`, `"gray4"` or `null`) | `{ok, data_url}` : a 648x480 PNG data URL of the screen's **idle** layout, drawn by the firmware's own standby renderer (`firmware/host_test/idle_render_host.cpp`) running on the host, so it is what the panel would show. Mono gives a 1-bit image, `gray4` four gray levels. `mode` `null` = the user's choice (`idle_modes`), else the mode the layout declares, else the first that compiles. **Live vs sample data:** the clock is live (current time, the host's time zone); weather, Steam and every other remote value come from the package's `sample-data.json` (placeholders where it has none), because the host does not fetch them for a preview. The renderer is a prebuilt static x86_64 binary shipped in the zip as `bin/idle_render_host` (SteamOS has no compiler; build it with `decky/scripts/build_idle_render.sh`). Decky's extraction may drop the exec bit, so the plugin copies it once into a private `0700` directory (`<settings dir>/.bin`) and runs that copy (argv list, empty environment, 10 s timeout). Only the declarative idle layout is rendered: a package's `screen.py` is never run, and a code package that is not approved is refused (`"<id>: needs approval before it can be previewed"`). Wi-Fi password and Steam key are never passed to the renderer. Errors `{ok: false, error}`: not installed, no idle layout (`"<id>: has no idle layout"`), `mode must be 'mono' or 'gray4'`, `cannot be shown in <mode> mode`, layout does not compile, bad `sample-data.json`, `idle preview renderer is not installed` (binary missing), `idle preview timed out`, `idle preview failed (...)` (crash or unreadable output). Never raises for bad input. |
| `get_firmware_status` | none | the firmware state, see [Firmware updates](#firmware-updates). Rejects when the plugin is not running. |
| `check_firmware` | none | asks GitHub for the newest signed release now and returns the same status (the check also runs at start and every 24 h). A failure is in `check`, not an exception. |
| `install_firmware` | `version` | must equal `latest.version` (the verified release), the panel must have been seen, no key-less build, no downgrade, nothing else running. `{ok: true}` at once; the work runs in a background thread and reports through the `firmware` event. |
| `cancel_firmware_install` | none | `{ok: true}` while the download or transfer runs; `{ok: false, error: "too late to cancel…"}` once the image is on the panel. |
| `push_idle_layouts` | none | Compile the selected idle screens and upload them now (this also happens by itself on every connect and on every change). `{ok: true, supported: true, idle}` once the upload started; `{ok: false, error, supported}` for: not connected, firmware without `idle_storage` (`"this firmware has no idle storage: update the firmware"`), nothing selected, or a screen that does not compile / a store that is too big (nothing is sent in that case). The outcome is in `get_status().idle`. |

Code screens run through `PackageStore.render` (approval re-checked against the bytes on disk, guarded child process). The live
data snapshot is passed to them as JSON; datetimes such as `time.now` arrive as ISO 8601 strings.

## Events (backend to frontend)

Subscribe with `addEventListener(name, cb)` from `@decky/api`; the frontend wrapper is `onEvent`.

| Event | Payload | Emitted |
|-------|---------|---------|
| `status` | the `get_status` dict | by a 1 s watcher when `ui_state`, `current_screen`, `pinned`, `now_playing`, `profile`, `fw_version`, `last_error`, `last_frame_at` or `reconnects` change (so about once per panel refresh), and after every successful change |
| `settings_changed` | none | after `set_settings`, `set_screen_settings`, `pin_screen`, `unpin_screen`, `set_rotation`, `uninstall_package` |
| `screens_changed` | none | after `set_screen_enabled`, `import_package`, `catalog_install`, `approve_package`, `uninstall_package` |
| `firmware` | the `get_firmware_status` dict | on every change of the check or the install (state changes, and progress at most once per percent) |

Failed calls emit nothing.

## UI

Source: `decky/src/`. Quick-access panel = `QuickPanel.tsx`; the full pages live behind the route `/inkterface`
(`MainPage.tsx`, a `SidebarNavigation`). All pages load on mount, refresh on the events above, and show a clear error line
instead of crashing when the backend is unreachable. Mutations show the backend's `error` text under the control.

### Quick-access panel

- **Status line**: `Connected · firmware <v>`, `Connecting to the panel…`, `Waiting for panel · plug in the Inkterface USB cable`,
  `Firmware too old · update the panel firmware` (with the engine's reason in red), `Stopped`, or
  `Cannot reach the Inkterface backend: <reason>`.
- When connected: **live preview** of the current screen (`preview_screen`, refreshed on every finished panel frame),
  the screen name (`(pinned)` when pinned), **Pin screen** dropdown (`Auto (rotation)` + every enabled active screen),
  and **Open Inkterface settings** (closes the side menu, navigates to `/inkterface`; both calls are guarded).

### Live Screens and Standby Screens: one flow, one component set

The sidebar pages are Live Screens, Standby Screens, Screen Store, Firmware, Settings and About. Live Screens and Standby Screens
use the same components from `src/components/ScreenList.tsx` (`StatusCard`, `OrderedList`, `AvailableList`), so they behave identically:

- a **status card** (plain panel picture, one sentence, a few facts);
- an **ordered list** of the screens that are IN (Live Screens: the rotation; Standby Screens: the standby selection), each a row: the **order track** (up chevron, position
  number, down chevron; 44 x 40 px hit areas; no up on the first row, no down on the last), the plain picture, name, badges, description, origin, one **Settings** button
  (opens a modal sheet) and a **"..."** menu (page entries such as Pin, then Move up, Move down, Remove);
- below it, the **available** screens, the same row with a blue primary **Add**.

Ordering: chevrons (touch, mouse, D-pad), **L1 / R1** move the focused row, **X** starts Reorder mode (D-pad moves, **A** keeps, **B** puts it back), **Y** removes the screen from the list (a green note with **Undo**; the last screen of the rotation is refused), the **Menu** button opens the "..." menu. **A** only activates the focused control. The
change is applied at once and rolled back if the backend refuses; the moved row keeps focus, slides in 220 ms (no slide under `prefers-reduced-motion`) and a status line says
`<name> is now N of M`. The focused row shows a hint legend (L1 / R1 / X / Y Remove / Menu More; D-pad / A / B in Reorder mode) and tells Steam's footer legend the same.

A page only supplies slots (`Slots` in `components/ScreenList.tsx`): the picture (`preview_screen` or `preview_idle_screen`), badges, notes, extra menu entries and the settings
sheet body (a component that loads its own data, because a modal is outside the page's tree). Buttons are a wrapping flex row inside the row, never a Steam `Field`; every control
is a `DialogButton` or a Steam field, so the D-pad reaches all of them.

#### Live Screens page

- **Panel** card: the current screen's picture with the LED, `Panel connected` / the state, `Showing <screen>` (pinned says so), facts Firmware, Wi-Fi, Standby after, Cycle. *On panel* follows the live `status` event. A line under it points to the Standby Screens page.
- **In rotation**: summary (`3 screens, 30 s each. A full cycle takes 1 min 30 s.`), pin note, seconds-per-screen slider (5..300), then the
  ordered list (`set_rotation`; ids that are not installed are hidden). Remove keeps the screen installed and enabled.
- **Available**: every screen with an `active` section that is not in the rotation. **Add** switches the screen on first when it is off
  (`set_screen_enabled`) and then appends it (`set_rotation`); a code package that waits for approval cannot be added. Idle-only screens are not
  listed; one line points to the Standby Screens page.
- **Settings** sheet of a row (Live Screens, rows in the rotation): **Enabled** switch (screen-only) and the form **generated from the screen's `settings` schema** (`bool` switch, `number`
  slider when min and max exist else a numeric field, `enum` dropdown, `string` text field). *Save settings* is enabled only when something
  changed, *Reset to defaults* only when something is not at its default. Validation messages come from the backend.
- Code that is not approved, or not switched on, gets no thumbnail (the reason is shown) and is never rendered.
- Loading shows `Loading…`; a failed load shows the reason and **Try again**; a failed refresh keeps the list and shows `Refresh failed: …`.

#### Standby Screens page

- **Overview**: *Panel* (connected with its firmware, not connected, or a firmware without `idle_supported`; an unreadable status locks the page),
  *Standby screens* (from `status.idle`: sending, waiting, on the panel with a storage bar, or the error), and one *Wi-Fi* status row with
  **Open Wi-Fi settings** (navigates to `/inkterface/settings`, whose first group is Connections). No Wi-Fi form here.
- **Standby Screens**: the ordered list (`set_settings {idle_screens}`; the first is shown first). Each card shows the render-mode badge and
  *On panel* once the panel acknowledged it; its **Settings** sheet holds the **4-gray rendering** switch (`idle_modes`) where the layout compiles in both
  modes. With several screens: `Order: A → B` and **Change screen every** (`idle_rotate_s`). **Send standby screens to the panel** (`push_idle_layouts`).
- **Pictures**: `preview_idle_screen(id, mode)`, the firmware's own standby renderer run on the host, in the mode the card will be uploaded in (a
  mode change re-renders). The clock is live; weather and Steam values are the package's `sample-data.json`. The page says so.
- **Available standby screens**: the rest, the same card with **Add**. A code package that waits for approval cannot be added and gets no picture.

#### Idle layouts on the panel

`get_status().idle`: `{state, error, ids, on_panel, modes, bytes, free, timeout_s}`. `state`: `none` (nothing selected), `pending` (waiting for
the panel), `uploading`, `ok`, `error`, `unsupported` (firmware without idle storage). `ids` are the selected screens in order, `on_panel` the
screens in the last store the panel acknowledged, `bytes` the size of the store, `free` the capacity the panel reported. It never contains the
layouts or any secret.

The store (docs/idle-mode.md) is built from the selected screens plus: the host's POSIX time zone (from the system zone file), location and
units, Steam key and SteamID, Wi-Fi SSID and password. Secrets go to the panel only: they are never logged, returned or put in an event.

### Settings

One page, three groups (`Group` in `components/ui.tsx`: a heading, an optional one-line intro, then Steam's own sections). Connections comes first so the
Standby Screens page's link lands on it.

1. **Connections** (`components/Connections.tsx`), all usable with a controller:
   - **Panel Wi-Fi** (`WifiSection.tsx`): status row, **Use the Steam Machine's network** (fills the name field from `wifi_current`, or lists the remembered networks when the
     machine is on a cable; it saves nothing), the name (`wifi_ssid`), a note that the password cannot be copied and that the panel only joins 2.4 GHz networks, and the password as a **secret field**.
   - **Steam** (`SteamSection.tsx`): who the saved account is (persona name and avatar from `steam_profile`), **Use my Steam account** (reads `App.m_CurrentUser.strSteamID`,
     fallback `SteamClient.User.RegisterForCurrentUserChanges`, saves it), a field that takes a profile link / SteamID64 / SteamID3 / custom name (`resolve_steam_id`, then saved as a SteamID64),
     where to get an API key with a **QR code** (`QrCode.tsx`, `qrData.ts`), the key as a **secret field**, **Test key** (`test_steam_key`: "Works: <name>" or the reason), and a note that
     game details must be public for Steam Stats.
   - **Weather** (`WeatherSection.tsx`): the chosen place by name ("Kraków, Poland"), **Search** by town name (`search_places`), **Use approximate location** (`locate_by_ip`; says that
     ipwho.is gets the IP address, shows the answer for confirmation), **Remove location**, **Units** (the first place ever chosen sets them from the system locale: imperial for US-style locales),
     and **Advanced: type coordinates**.
2. **Behaviour**: standby timeout, **Full refresh to clear ghosting** (a dropdown: Never / every 5 / 10 (default) / 15 / 30 min / hour / 2 hours; a value set by hand in `settings.json` also shows up in the list; see [ghost-clear.md](ghost-clear.md)), auto-switch to Now Playing. (Rotation interval is on the Live Screens page.)
3. **Data and uninstall**: *delete my settings and packages when the plugin is uninstalled*. Set apart (divider and extra space) so a destructive
   switch is not next to the credentials above it.

A failed load shows the reason and **Try again**.

- **Secret field** (`SecretField.tsx`): a masked password input (Steam's text field; the controller keyboard opens on activation), never prefilled
  (the backend only says `"set"` or `"unset"`), a *saved / not set* status row in words, **Save** (disabled while empty or invalid), a separate
  **Remove**. The draft is cleared as soon as it is sent and the value is scrubbed from any error text. Nothing logs it.

### Screen Store

`StorePage.tsx`, sidebar entry after Standby Screens. One page with two tabs (`role="tablist"`): **Browse** (the signed
catalog) and **Installed** (the installed packages).

- **Browse** (default) is a responsive grid of capsule cards (Steam Library style): a plain thumbnail
  (`catalog_preview`), name, author, version, and the badges **Installed**, **Update available**, **Runs code**,
  **Needs approval**, **Incompatible**. Pressing **A** / clicking a card opens a **details sheet** (`showModal`, so it
  loads its own data) with the full facts and the action buttons **Install**, **Update**, **Uninstall** (asks first),
  **Review and approve**. A bottom hint bar shows **A Details · B Back · Y Refresh** (Y refreshes). On open the page
  shows the saved catalog (`catalog_list`), then refreshes once (`catalog_refresh`) and lists again; a failed refresh
  keeps the saved list and says `Showing the saved catalog from <time>: <error>` with **Retry**. A **key-less build**
  (a build with no signing key; the shipped one has the maintainers' key) is shown as a calm, intentional empty state — "The Screen Store is not set up yet"
  with an Open Settings button, **not** a red error. Installing a code screen opens the approval dialog right away;
  closing it leaves the screen installed but off, with **Review and approve**.
- **Installed** is the installed non-built-in packages (author, id, version, code state, approval state, Review and
  approve, Uninstall) and, **only** with Developer mode on, the zip/URL import form (`InstalledPackages.tsx`). See
  [Installed packages](#installed-packages) below.

Catalog trust model and the exact callables: [screen-catalog.md](screen-catalog.md).

**Settings also holds:**

- **Screen catalog**: the catalog URL (empty field = default, https only), **Save** and **Reset to default**. A mirror can only serve content signed
  by the bundled key.
- **Developer mode** (off by default, with a warning): turns on importing a zip or an https link under Screen Store → Installed. Code screens still need
  approval, but nothing reviews what you import.

### Installed packages

The **Installed** tab of the Screen Store page (`InstalledPackages.tsx`); there is no separate Packages page in the sidebar.

- **Install a screen package** (only with Developer mode on; otherwise a note points to Settings → Developer, with an
  Open Settings button): path or `https://` URL field + Import. A code package opens the **approval dialog**.
- **Approval dialog**: title, a warning that the package **runs Python code with your user's permissions and is not sandboxed**
  (it can read your files and use the network), **author, id, version, SHA-256 of `screen.py`**, `Approve and enable` / `Cancel`.
  Approve sends exactly the shown `(id, version, sha256)`; if the file changed since, the backend refuses and the page shows why.
  Closing the dialog approves nothing; the package stays installed but disabled and shows **Review and approve**.
- **Installed packages**: author, id, code or no code, approval state, Uninstall (asks first). Always visible, with or without Developer mode.

### Firmware

`FirmwarePage.tsx`, sidebar entry **Firmware**. Everything comes from `get_firmware_status` and the `firmware` event (refetched on `status`).

- **Installed / Latest / Last checked** fields. Installed says `(development build)` for a `g<hash>` firmware and `(panel not connected)`.
  Latest says `no release published` or `unknown` (the check failed).
- **No signing key** (`trust.state == "no_key"`): a warning banner `No signing key yet. …` that cannot be missed; Install and Check are disabled.
  This is the state of every build until a maintainer sets the production key (docs/ota.md).
- **Protocol mismatch** (the panel's HELLO speaks another protocol, engine state `firmware_too_old`): a banner, and the quick-access
  panel shows an **Update panel firmware** button that opens this page. The install works in that state (OTA frames stay compatible).
- **Update available**: changelog, **View release on GitHub**, **Install firmware X**. Install opens a confirm dialog (from → to, takes
  about a minute, keep the cable plugged in, automatic rollback after ~3 min if the new firmware does not start); Cancel sends nothing.
- **Progress**: bar with percent while sending (indeterminate while downloading / preparing / waiting for the reboot) and **Cancel update**
  (off once the image is on the panel, with the reason).
- **Outcomes**: success (`installed and confirmed`), failure (the reason in red + "keeps its previous firmware"), failure after the reboot
  (`rollback_pending`: the panel rolls back by itself), cancelled.

### About

Plugin version, panel firmware, protocol version, panel size, links (opened with `Navigation.NavigateToExternalWeb`, guarded).

## Firmware updates

Backend: `inkterface_core/firmware.py` (`FirmwareManager`, owned by `PluginCore`), on top of `inkterface_core/ota/` ([ota.md](ota.md)).
The plugin **never installs by itself**; only `install_firmware` (after the confirm dialog) does.

`get_firmware_status` returns:

| Field | Meaning |
|-------|---------|
| `installed` | `{version, dev_build, protocol_version, connected, seen}`: from the last HELLO (`seen` false = no panel since start) |
| `latest` | `{version, tag, changelog, size, page_url}` of the newest verified release, or `null`. A failed check keeps the last good one |
| `update_available` | newer than `installed` (a dev build always gets the release offered); false for same, older (see `note`) |
| `trust` | `{state: "ok" \| "no_key", message}`. `no_key` = `ota/trust.py` is empty: no network access, no install |
| `check` | `{at, ok, error, error_kind, next_at, checking, retry_at, limit_message}`. Runs at start, then every 24 h (1 h after a failure, or until the rate limit resets); a manual check within 60 s of a successful one is not repeated, and the check at plugin start is skipped when the cached list is under 24 h old. `error_kind` is the `OtaError` class name. `retry_at` (unix time, or null) and `limit_message` are set while GitHub's API rate limit applies, also when `ok` is true because the cached release list answered; no request is made before `retry_at` |
| `protocol_mismatch`, `mismatch_message` | the panel speaks another protocol than this plugin: update through this flow |
| `install` | `{state, version, done, total, percent, message, error, cancellable, rollback_pending}`, `state` one of `idle, downloading, preparing, sending, confirming, done, failed, cancelled` |

Install, in a worker thread: download + verify (signed manifest, size, sha256) → `Engine.pause()` (the engine closes the port, sends nothing, status
`paused`/`updating`) → `ota.stream.install` (OTA_BEGIN/DATA/END with resume after a dropped link, reconnect, wait for the new `fw_ver`, HELLO ack that
validates the image) → `Engine.resume()` (always, also after a failure or cancel). A panel that does not confirm rolls back by itself; the UI says so.
Time (`clock`, `sleep`), the GitHub `fetch` and the key (`public_key`) are injectable on `PluginCore` for tests.

## SteamClient now-playing wiring (`src/steam.ts`)

Installed once when the plugin loads (not per panel open) and removed in `onDismount`.

- `SteamClient.GameSessions.RegisterForAppLifetimeNotifications`: `bRunning` true calls `set_now_playing(appid)`; `bRunning` false
  calls `set_now_playing(null)` **only if that appid is the current one** (another game quitting must not blank the running one).
- A game already running at load is found through `Router.MainRunningApp`.
- Duplicate events send nothing; malformed notifications (no/zero/negative/non-integer appid) are ignored; non-Steam shortcut ids (above 2^31) work.
- Every access is guarded: a missing `SteamClient`, `GameSessions`, method, a throwing `register`/`unregister`/`Router` getter, or a rejecting
  `set_now_playing` leaves the plugin working; the panel just does not auto-switch.

## Secrets

The only secret is the Steam Web API key.

- Stored in `host.json` in the plugin's settings dir. Written with mode **0600 from the first byte** (temp file made by `mkstemp`, `fchmod 0600`,
  fsync, atomic rename), directory 0700. The engine's `settings.json` never contains it.
- **Never returned**: `get_settings` says `"set"` / `"unset"`; no other callable includes it. The UI never preloads it into a field, sends it once and clears the field,
  and the field is a password input.
- **Never logged**: the logs record which setting names changed, not values. Validation errors never contain the value.
  `SteamWeb` already masks the key in its error messages; `get_status().last_error` and settings errors are additionally scrubbed of the stored key.
- A corrupt `host.json` is moved to `host.json.corrupt` (it may hold the key) rather than parsed leniently or logged.
- It reaches only the Steam Web API source. Package code never receives it (screens get the data snapshot, which holds no credentials, and an empty environment).

## Tests

```bash
python3 -m pytest decky/tests/plugin decky/tests/test_main.py -q      # backend against a fake decky module + emulated panel
(cd decky && npx pnpm@9 test)                                          # frontend, vitest (jsdom, mocked @decky/api and @decky/ui)
(cd decky && npx pnpm@9 coverage)                                      # frontend coverage
(cd decky && npx pnpm@9 typecheck && npx pnpm@9 build)                 # tsc --noEmit, rollup -> dist/index.js
python3 decky/scripts/make_zip.py                                      # decky/out/inkterface-decky.zip
```

Backend tests (`decky/tests/plugin`) run `main.py` against a fake `decky` module (logger, dirs, `emit`), a real `PluginCore`, a real
`Engine` on an `EmulatedLink`/`EmulatedPanel`, and an offline registry. They cover each callable (happy path, invalid input, no panel,
old firmware), events, logging, clean unload (thread leak check on every test), uninstall with and without purge, restart persistence, corrupt files, disk errors, and that the Steam key
never appears in a response, event, log record or file other than `host.json` (0600).

Firmware updates have their own suites: `decky/tests/test_ota_plugin.py` (PluginCore end to end over the emulator with a TEST-ONLY-signed
fixture release: update found, install with hash check, reboot + HELLO + confirm, no key, bad signature, downgrade, link drop and resume, cancel,
engine paused and resumed, daily schedule on a fake clock, GitHub errors, protocol mismatch, shutdown mid-install), `decky/tests/test_firmware_unit.py`,
`decky/tests/plugin/test_firmware_callables.py` (the Decky callables and the `firmware` event), `decky/tests/engine/test_engine_pause.py`, and
`decky/test/FirmwarePage.test.tsx` (every UI state).

## What needs a real Steam Deck / Steam Machine to verify

Nothing below can be checked in the emulator or in jsdom.

- The real `@decky/ui` components: focus/gamepad navigation in `SidebarNavigation`, slider and dropdown behaviour with a controller, `showModal` / `ConfirmModal`
  (our code does not depend on whether `ConfirmModal` closes after `onOK`; errors are shown on the page, not in the dialog), on-screen keyboard for text fields.
- `routerHook.addRoute("/inkterface")`, `Router.CloseSideMenus`, `Navigation.NavigateToExternalWeb`, `Router.MainRunningApp` in the current Steam client build.
- `SteamClient.GameSessions.RegisterForAppLifetimeNotifications` payloads (launch and exit, Proton titles, non-Steam shortcuts, game switching).
- Decky's real Python 3.14 bundle: `asyncio.to_thread`, `decky.emit`, the vendored Pillow loading, `python3` on `PATH` for code screens (inside the frozen loader `sys.executable` is not Python).
- `DECKY_PLUGIN_SETTINGS_DIR` ownership and permissions (0600 on the key file) and whether the plugin runs as `deck`/root (`plugin.json` flags).
- USB: panel discovery by VID 303a, hot-plug, suspend/resume of the Steam Machine, behaviour while a game runs (CPU cost of rendering at 1 s, the 5 s ACK timeout).
- Steam Web API and Open-Meteo reachability from the Deck; a real key.
- Installing the zip from `make_zip.py` through Decky's "install from zip", reloading and uninstalling the plugin.
- A firmware install on the real panel from the plugin: transfer time over the real link, the USB re-enumeration after the reboot (the updater
  reopens the port by VID 303a and waits up to 30 s for HELLO, 120 s for the new version), `Engine.pause()` releasing the port on the Steam Machine,
  the confirm dialog and progress bar with a controller. Needs a release signed with the production key (none exists yet).
- Idle mode end to end on the real panel: upload on connect, the panel's own Wi-Fi/weather/Steam fetch, the picture on the glass.
