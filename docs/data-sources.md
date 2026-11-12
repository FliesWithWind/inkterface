# Host data sources

Code: `decky/py_modules/inkterface_core/sources/` (stdlib only; loads in Decky's bundled Python).
Tests: `decky/tests/sources/`. Live check: `python3 decky/tools/dump_sources.py` (see the end).

## Model

```python
from inkterface_core import sources as S

reg = S.default_registry(settings)   # time, system, steam_local, steam_web, weather
reg.start()                          # one daemon thread per source (or call reg.poll_due() yourself)
snap = reg.snapshot()                # flat dict, safe to read from any thread
reg.set_now_playing(730)             # frontend: SteamClient game start; None on exit
reg.stop()
```

The snapshot is a **flat dict keyed by the binding paths** below. It is flat because
`system.cpu.load` is a number and `system.cpu.load.history` a list. Every path is always
present; unknown is `None` (a history is `[]`). The screen engine may rely on this. Exact keys: `time.now`, `time.tz`,
the 11 `system.*` metrics and their `.history` siblings, `steam.now_playing`, `steam.user`, `steam.recent`, `steam.friends_online`,
`weather` (a dict, nested access like `weather.forecast[0].icon` is the engine's job) and `_meta`; a test pins this set.

* `time.now` is an aware `datetime`; use `S.dumps(snap)` to get JSON (datetimes become ISO strings).
* `<path>.history`: last **60** numeric samples of that metric, **oldest first**, one per poll of
  its source (system: about 1 per second). `None` samples are skipped, so the list can be
  shorter than 60 or have gaps in time.
* `_meta`: `{"sources": {name: {stale, age, error, warning, paths}}, "stale": [paths of stale sources]}`.
  `age` is seconds since the last good poll (`None` = never). `error` is the last poll failure
  (cleared on success). `warning` means degraded but still delivering (for example one Steam endpoint failing).

### Failure handling

* A source that raises (or returns a non-dict) never affects the others. Its values stay
  as last seen, flagged **stale** after `3 x interval`, and become `None` after `10 x interval` (history is kept).
* Failed polls back off exponentially: next try after `interval x 2^failures`, capped at the source's `max_backoff`
  (900 s, system 30 s).
* When two sources emit the same top-level key (`steam.*` from `steam_local` and `steam_web`), they are merged:
  dicts field by field, a `None` never overrides a value, otherwise the later source wins. Order is
  time, system, steam_local, steam_web, weather, so Web API data beats local files and local files fill in
  whatever the Web API does not know (or when it is down, expired, or not configured).

### Change events and fast sampling (used by the engine, see "Event-driven rendering" in [engine.md](engine.md))

* `reg.subscribe(cb)` / `unsubscribe(cb)`: `cb()` runs on the polling thread after a poll changed a *watched* key
  (value compared with `!=`; a number's `.history` key counts as changed with it). Keep `cb` quick; exceptions are logged and swallowed.
* `reg.watch(keys)`: the snapshot keys a consumer displays (`None` = everything, empty = nothing, the default). A source
  with a `fast_interval` that owns a watched key is polled that often instead of `interval`. Today: `system` 0.1 s,
  `time` 0.25 s (`time.now` has whole-second resolution), `steam_local` 2 s. Network sources have none.
* The history ring keeps its normal cadence (one sample per `interval`) while a source is fast-sampled, so 60 samples are still 60 s.
* `Source.instant = True` (`steam_local`): `reg.set_now_playing()` polls it on the caller's thread, so the new game's name and box art are
  in the snapshot when it returns. Other sources are woken and poll on their own thread. One poll per source runs at a time.
* `system.disk.temp` is read at most every 10 s (an NVMe sensor read costs ~3 ms, more than all other metrics together).

### Settings (`default_registry(settings)`, all optional)

| Key | Type | Used by |
|-----|------|---------|
| `timezone` | IANA name, e.g. `Europe/Berlin` | `time.*`. Read from the system's tz database (`/usr/share/zoneinfo`) when Python has no `zoneinfo`, as in Decky. Unknown or no tz data: system zone plus a `warning`. |
| `steam_dir` | path | `steam_local`. Default: `~/.local/share/Steam`, `~/.steam/steam`, Flatpak Steam. |
| `steamid64` | 17 digits | `steam_web` (required there); `steam_local` uses it to pick `localconfig.vdf`. |
| `steam_api_key` | string | `steam_web`. Never logged; stripped from error messages. |
| `weather_lat`, `weather_lon` | number or numeric string | `weather` |
| `units` | `metric` (default) or `imperial` | `weather`: C / F |

All HTTP goes through one injectable `fetch(url, timeout) -> bytes` (raises `FetchError`);
tests inject fakes, the default is `urllib_fetch` (http/https only, 8 MB cap).

## Paths

"None when" lists the conditions that give `None`. Any missing, unreadable (EIO, ENODEV) or non-numeric sensor file is `None`, never an exception.

### time (source `time`, poll 1 s)

| Path | Type | Notes |
|------|------|-------|
| `time.now` | aware `datetime` | Settings zone, else system zone. Never `None` after the first poll. |
| `time.tz` | string | IANA name (`Europe/Berlin`) from settings, `$TZ`, or `/etc/localtime`; else the abbreviation (`CET`). |

### system (source `system`, poll 1 s; `/proc` and `/sys`, root injectable)

| Path | Unit | Source | None when |
|------|------|--------|-----------|
| `system.cpu.load` | % 0-100 | `/proc/stat`, first `cpu` line. Delta of busy/total jiffies between polls; iowait counts as idle, steal as busy. | First poll (no baseline); unreadable or malformed `/proc/stat` before any value. Counter wrap/reset (delta < 0) or zero delta keeps the last value and resyncs. |
| `system.cpu.temp` | C | hwmon `k10temp`, then `zenpower`, then `coretemp`; label `Tdie`, `Tctl`, `Package id 0`, else lowest `temp*_input`. | No such hwmon or unreadable. |
| `system.cpu.clock` | MHz | Mean of `cpu MHz` in `/proc/cpuinfo`; fallback mean of `cpufreq/scaling_cur_freq` (kHz). | Neither present. |
| `system.gpu.load` | % | `/sys/class/drm/card<N>/device/gpu_busy_percent` of the first card that has it (connector entries such as `card0-DP-1` ignored). | No amdgpu card. |
| `system.gpu.temp` | C | The `amdgpu` hwmon whose `device` is the same PCI device as the chosen card (else the first `amdgpu`); label `edge`, `junction`, else `temp1`. | No amdgpu hwmon. |
| `system.gpu.clock` | MHz | Active (`*`) level of `pp_dpm_sclk`; fallback hwmon `freq*_input` (Hz) labelled `sclk`. | Neither. |
| `system.gpu.mem_clock` | MHz | Active level of `pp_dpm_mclk`; fallback hwmon `freq*_input` labelled `mclk`. | Neither. |
| `system.mem.used_pct` | % | `/proc/meminfo`: `(MemTotal - MemAvailable) / MemTotal`; without `MemAvailable`, `MemFree + Buffers + Cached`. | `MemTotal` missing or 0. |
| `system.fan.rpm` | RPM | `fan*_input` of `steamdeck_hwmon` if present, else the highest fan input of any hwmon. 0 is a valid value (fan stopped). | No fan input anywhere. |
| `system.power.package_w` | W | `power1_average`, else `power1_input` (microwatts / 1e6): the GPU/APU hwmon first, then any other hwmon. | No power sensor. |
| `system.disk.temp` | C | Extra, not in the v1 binding list. Hottest `nvme` hwmon, label `Composite`, else `temp1`. | No nvme hwmon. |

Each numeric path above also has `<path>.history`.

Chosen without a name match on purpose: a desktop with an NVIDIA card0 and an AMD card1 uses card1; with
an iGPU plus dGPU the first card with `gpu_busy_percent` wins (sorted numerically). NVIDIA has no sysfs
metrics, so `system.gpu.*` is `None` on NVIDIA-only machines.

### steam_local (source `steam_local`, poll 10 s; also polled right after `set_now_playing`)

Reads the Steam directory; files are cached by mtime and a half-written file keeps the previous parse (plus a `warning`).

| Path | Type | None when |
|------|------|-----------|
| `steam.now_playing` | `{appid, name, box_art_path, session_minutes, total_minutes, last_achievement}` | `None` when no game was reported via `set_now_playing`. `name`: from `steamapps/appmanifest_<id>.acf` in any library (`libraryfolders.vdf`), `None` if not installed. `box_art_path`: see below, `None` if not cached. `session_minutes`: whole minutes since the id was set. `total_minutes`: `Playtime` in `localconfig.vdf`, `None` if absent. `last_achievement`: always `None` locally; filled by `steam_web`. |
| `steam.user` | `{persona, level, games_owned, hours_total}` | `persona`: `PersonaName` of the most recent login in `config/loginusers.vdf` (`MostRecent`, else latest `timestamp`). `hours_total`: sum of `Playtime` over `localconfig.vdf` apps / 60, 1 decimal (`None` without that file). `level`, `games_owned`: always `None` locally. Whole value `None` if Steam is not found. |
| `steam.recent` | list of `{appid, name, minutes_2w, minutes_total}` | Apps with `Playtime2wks > 0`, most played first (ties: most recently played), at most 5. `name` is `None` for apps that are not installed. `[]` with no data, `None` if Steam is not found. |

Box art lookup in `appcache/librarycache/`, first hit wins, `library_600x900.jpg` before `library_capsule.jpg` (300x450, the only portrait art many apps have): `<appid>_library_600x900.jpg` (old flat layout),
`<appid>/library_600x900.jpg`, `<appid>/<hash>/library_600x900.jpg` (newer layouts).

`set_now_playing` accepts `None` or a positive integer (or digit string); anything else raises `ValueError`
because the id ends up in file paths. Steam not found is a `warning`, not an error.

### steam_web (source `steam_web`, poll 120 s; optional)

Needs `steam_api_key` and `steamid64`. Without either, no request is made and everything is `None`.
A `steamid64` that is not 17 digits is a source error. Profile must be public for most fields.

| Path | Type | Endpoint / None when |
|------|------|----------------------|
| `steam.user` | same as above | `persona`: `GetPlayerSummaries`. `level`: `GetSteamLevel`. `games_owned`, `hours_total`: `GetOwnedGames` (cached 1 h). Each field is `None` if its endpoint failed and was never successful; otherwise the last good value is kept. |
| `steam.recent` | same as above | `GetRecentlyPlayedGames`. Overrides the local list when present, including an empty list. |
| `steam.friends_online` | int | `GetFriendList` then `GetPlayerSummaries` in batches of 100, counting `personastate > 0`. `None` if the friend list is private (HTTP 401) or the call fails. |
| `steam.now_playing` | partial dict | Only while a game is set: `{appid, name, total_minutes, last_achievement}` merged into the local dict (name and total fill gaps or win; `box_art_path` and `session_minutes` stay local). `last_achievement`: display name of the achievement with the latest `unlocktime` from `GetPlayerAchievements`; `None` if the game has no stats or the profile hides them (HTTP 400/403, not an error). Cleared when the game changes. |

If every endpoint fails the poll raises (so backoff applies and the last values go stale); if only some fail, the source
keeps working and sets `warning` to the failed endpoint list. HTTP 429/5xx and timeouts are failures like any other.

### weather (source `weather`, poll 900 s; Open-Meteo, no key)

One request: current conditions plus 4 daily rows, `timezone=auto`. `weather` is `None` until `weather_lat` and `weather_lon` are set.
Invalid coordinates (not numbers, lat outside +-90, lon outside +-180), unknown `units`, HTTP errors, timeouts and malformed or unexpected JSON are source errors.

| Field | Type | Notes |
|-------|------|-------|
| `temp` | number | Current, C or F per `units` |
| `condition` | string | WMO text, e.g. `Overcast`; `Unknown` for unlisted codes |
| `icon` | string | Engine icon name: `sun`, `cloud` or `rain`; `cloud` for unlisted codes |
| `code` | int or `None` | Raw WMO weather code |
| `high`, `low` | number or `None` | Today |
| `sunrise`, `sunset` | `"YYYY-MM-DDTHH:MM"` local to the location, or `None` | Today |
| `units` | `metric` or `imperial` | Echo of the setting |
| `forecast` | list of `{date, high, low, icon, code}` | Tomorrow and the 2 days after (3 entries; fewer if Open-Meteo returns fewer). |

Icons are limited to the screen engine's weather icons (`sun`, `cloud`, `rain`): WMO `0, 1` -> `sun`; `2, 3, 45, 48` (partly cloudy,
overcast, fog) -> `cloud`; every drizzle, rain, snow, shower and thunderstorm code -> `rain`. Use `weather.code` and `weather.condition`
when a screen needs the difference. The table is `_WMO` in `weather.py`; a test checks every Open-Meteo code maps to a valid name.

## Running the checks

```bash
python3 -m pytest decky/tests/sources -q                      # needs only pytest
python3 -m coverage run --branch --source=decky/py_modules/inkterface_core/sources -m pytest decky/tests/sources -q
python3 -m coverage report -m                                  # needs `pip install coverage`; currently 99 %

python3 decky/tools/dump_sources.py                            # real snapshot: time, system, local Steam
python3 decky/tools/dump_sources.py --live --lat 52.52 --lon 13.41          # + Open-Meteo
INKTERFACE_STEAM_KEY=... python3 decky/tools/dump_sources.py --live --steamid 7656... # + Steam Web API
```

`dump_sources.py` polls twice 1.2 s apart (CPU load is a delta) and redacts the key and anything shaped like a steamid64.
It never opens the panel.

Not covered by tests (needs the real thing): real Steam Web API responses (fixtures are modelled on the documented
shapes, the Open-Meteo fixture is a recorded response), NVIDIA metrics, and Steam Deck / Steam Machine sysfs layouts
(fake trees model the amdgpu, k10temp and steamdeck_hwmon layout from kernel docs and this dev machine).
