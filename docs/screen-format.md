# Screen format reference

A screen is a folder (a "package") that tells Inkterface what to draw on the 648x480 1-bit e-ink panel.
This page is the reference for people writing screens.

```
my-screen/
  screen.json     required
  assets/         images used by `image` widgets (optional)
  screen.py       code tier (optional, see "Code tier")
```

- The folder name must equal the `id` in `screen.json`.
- Two sections exist: `active` (drawn by the host while the machine is on) and `idle` (drawn by the
  panel firmware while the machine is off). A screen has either or both.
- **Active** is implemented by the host engine and documented in full below.
  **Idle** fields are defined and validated structurally, but rendered by firmware: see
  [Idle section](#idle-section).

The display is 1-bit: every pixel is black or white. Text is drawn without anti-aliasing, images are
dithered.

## Try a screen: the emulator

No panel or Steam Machine needed. From `decky/py_modules`:

```bash
# one package, with your own data
python -m inkterface_core.screens.emulate path/to/my-screen --data snapshot.json --out out.png

# data defaults to <package>/sample-data.json when --data is omitted
python -m inkterface_core.screens.emulate path/to/my-screen --out out.png

# every package in a folder (writes <folder>/<id>.png into ./out by default)
python -m inkterface_core.screens.emulate --all decky/screens --out out/

# override a screen setting (value is JSON, or a bare string)
python -m inkterface_core.screens.emulate path/to/my-screen --set time_format=%I:%M --set date_format=%d.%m
```

Exit code is 0 on success and 1 if any package is invalid. Every validation problem is printed with
the JSON path it concerns, for example:

```
screen.json: active.widgets[3].bind: unknown source 'cpu.tmp' (did you mean 'system.cpu.temp'?)
screen.json: active.widgets[5].x: widget extends past the 648px edge (x+w = 700)
```

`snapshot.json` is a JSON object holding the [data sources](#data-sources) your screen binds to.
Anything you leave out renders as `--`, so you can test the empty state the same way.

Tests: `decky/tests/screens/` holds golden-image tests for the built-in screens. After an intended
visual change run `pytest decky/tests/screens --update-goldens` (or set
`INKTERFACE_UPDATE_GOLDENS=1`) and review the PNG diff.

## screen.json

Unknown keys are rejected everywhere (a typo is an error, with a "did you mean" hint).

### Top level

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `schema` | int | yes | Must be `1` |
| `id` | string | yes | `a-z`, `0-9` and single `-`; must equal the folder name |
| `name` | string | yes | Display name, max 80 chars |
| `version` | string | yes | Semver `x.y.z` (pre-release/build suffix allowed) |
| `author` | string | yes | Shown to the user when approving code-tier packages, max 80 chars |
| `description` | string | no | Max 500 chars |
| `active` | object | one of `active`/`idle` | See below |
| `idle` | object | one of `active`/`idle` | See [Idle section](#idle-section) |
| `settings` | array | no | User-editable options, see [Settings](#settings) |

### `active` section

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `widgets` | array | required | Drawn in order, later widgets paint over earlier ones. Max 200 |
| `size` | `[648, 480]` | `[648, 480]` | Must be exactly this in v1 |
| `refresh_s` | number | `1.0` | How often the host re-renders, 0.1 to 3600 seconds |
| `code` | string | none | Path of `screen.py`, see [Code tier](#code-tier) |
| `triggers` | object | none | `{"on_game_launch": true}` switches to this screen when a game starts |

### Widgets

Every widget has these fields:

| Field | Type | Notes |
|-------|------|-------|
| `type` | string | One of the widget types below |
| `x`, `y` | int | Top-left corner in pixels, `>= 0` |
| `w`, `h` | int | Size in pixels, `>= 1`. `x+w <= 648` and `y+h <= 480` |
| `id` | string | Optional, unique within the screen |
| `invert` | bool | Default `false`. White ink on a black background. Images are not inverted |
| `border` | int | Default `0`, max `4`. Draws a frame of that thickness inside the widget box |
| `bind` | string | Data source path, for widgets that take one (see each widget) |

A widget draws into its own box and is clipped to it. An empty `text` widget with `invert: true` is a
black rectangle, which is how the built-in screens draw header bars and rules.

Missing or unusable data never raises an error: the widget shows `--` (graphics widgets show an empty
frame or a centred `--`).

**Fonts.** `font` values look like `regular-16` or `bold-32`: style, a dash, a pixel size from 6 to 200.
The bundled font is Inkterface Mono (Regular and Bold): IBM Plex Mono with its hinting instructions removed and renamed, so FreeType
hints it with its auto-hinter and every letter of a size gets the same stem width. It is
monospaced: a glyph is 0.6 x size px wide.
Licence: SIL Open Font License 1.1, copyright IBM Corp. (Reserved Font Name "Plex", hence the new name); the licence text ships next to the font files in
`decky/py_modules/inkterface_core/screens/fonts/OFL.txt`.

**Templates.** Options marked *template* may contain `{source}` references, optionally with a Python
format spec: `"CPU {system.cpu.load:.0f}%"`. Use `{{` and `}}` for literal braces. A reference to a
missing value becomes `--`. A series cannot be used in a template.

**Number format.** `format` options (and `label_format`) contain exactly one field and optional
surrounding text: `"{:.1f}"`, `"{:.0f} W"`. Attribute access (`{0.real}`) is not allowed.

#### `text`

Static or templated text.

| Option | Type | Default | Notes |
|--------|------|---------|-------|
| `text` | string, *template* | required | Max 500 chars |
| `font` | font | `regular-16` | |
| `align` | `left` `center` `right` | `left` | |
| `valign` | `top` `middle` `bottom` | `top` | |
| `wrap` | bool | `false` | Word-wrap to the box width (long words are split) |
| `ellipsis` | bool | `true` | Shorten with `…` when text does not fit (width, or height when wrapping). `false` clips |
| `max_lines` | int 1-50 | box height | Cap on wrapped lines |

#### `number`

A bound number with optional unit. If the text is wider than the box the font shrinks (down to 8 px).

| Option | Type | Default | Notes |
|--------|------|---------|-------|
| `bind` | number source | required | |
| `format` | string | none | e.g. `"{:.1f}"`. Overrides `precision` |
| `precision` | int 0-6 | `0` | Decimals when `format` is not set |
| `unit` | string, *template* | `""` | Drawn smaller, on the same baseline |
| `font` | font | `bold-24` | |
| `align` | `left` `center` `right` | `left` | |
| `valign` | `top` `middle` `bottom` | `middle` | |

#### `sparkline`

A line chart of a history series. Points are spread evenly across the width.

| Option | Type | Default | Notes |
|--------|------|---------|-------|
| `bind` | series source | required | e.g. `system.cpu.load.history`. Non-numbers are skipped; fewer than 2 points shows `--` |
| `range` | `[min, max]` | data min/max | Fixed scale, `min < max`. Values outside are clamped |
| `window` | int 2-10000 | all points | Use only the last N points |
| `show_labels` | bool | `false` | Prints the scale max (top left) and min (bottom left) |
| `label_format` | string | `"{:.0f}"` | Format of those two labels |
| `thickness` | int 1-4 | `1` | Line width |
| `fill` | bool | `false` | Sparse dotted fill under the line |
| `marker` | bool | `true` | Small square on the latest point |

#### `bar`

A progress bar.

| Option | Type | Default | Notes |
|--------|------|---------|-------|
| `bind` | number source | required | |
| `range` | `[min, max]` | `[0, 100]` | Value is clamped |
| `orientation` | `horizontal` `vertical` | `horizontal` | Vertical fills from the bottom |

#### `gauge`

An arc gauge with the value printed in the centre. The box is treated as a square (the shorter side).

| Option | Type | Default | Notes |
|--------|------|---------|-------|
| `bind` | number source | required | |
| `range` | `[min, max]` | `[0, 100]` | |
| `start_angle` | number -360..360 | `135` | Degrees, `0` = 3 o'clock, clockwise |
| `sweep` | number 10-360 | `270` | Degrees covered by the full scale |
| `thickness` | int 2-40 | `12` | Arc width, limited to the radius |
| `format` | string | none | Overrides `precision` |
| `precision` | int 0-6 | `0` | |
| `unit` | string, *template* | `""` | |
| `font` | font | `bold-32` | Shrinks to fit inside the arc |

#### `icon`

One of the built-in 1-bit icons, scaled to the shorter side of the box and centred.
Names: `cpu`, `gpu`, `thermometer`, `fan`, `bolt`, `gamepad`, `sun`, `cloud`, `rain`, `clock`.

| Option | Type | Default | Notes |
|--------|------|---------|-------|
| `name` | icon name | none | Fixed icon |
| `bind` | string source | none | The source value is used as the icon name (e.g. `weather.icon`). If it is not a known name, `name` is used |

At least one of `name` and `bind` is required. With neither usable the widget shows `--`.

#### `image`

A bitmap, scaled into the box and converted to 1-bit.

| Option | Type | Default | Notes |
|--------|------|---------|-------|
| `src` | string | none | File inside your package's `assets/` folder |
| `bind` | path source | none | A file path from the data (e.g. `steam.now_playing.box_art_path`) |
| `fit` | `contain` `cover` `fill` | `contain` | `contain` keeps aspect ratio with letterboxing, `cover` fills and crops the centre, `fill` stretches |
| `dither` | `floyd-steinberg` `threshold` | `floyd-steinberg` | `threshold` is a hard black/white cut at 50 % |

Exactly one of `src` and `bind` is required. Transparent pixels count as white. A relative bound path
is resolved against the folder of the snapshot file (emulator) and ignored otherwise.

**Asset rules** (checked when the package loads): path relative to `assets/`, no `..`, no absolute path,
no backslashes, no symlink leaving the package, file must exist and be a readable image, at most
2 MiB and 16 million pixels. Images supplied through `bind` may be up to 8 MiB.

#### `clock`

A formatted time.

| Option | Type | Default | Notes |
|--------|------|---------|-------|
| `bind` | time source | `time.now` | |
| `format` | string, *template* | `%H:%M` | `strftime` format, e.g. `%A, %d %B %Y` |
| `timezone` | string, *template* | `""` | IANA name such as `Europe/Berlin`. Empty means `time.tz` (when showing `time.now`), else no conversion |
| `font` | font | `bold-48` | |
| `align` | `left` `center` `right` | `center` | |
| `valign` | `top` `middle` `bottom` | `middle` | |

A time value is epoch seconds (UTC) or an ISO 8601 string. A string without an offset is taken to be
wall-clock time already: the zone is attached, not applied. A literal `timezone` is checked when the
package loads.

#### `now-playing`

Box art on the left, then game name, session time, total time and latest achievement. It reads fixed
sources (`steam.now_playing.*`) and takes no `bind`. If no game is running the lines show `--`.

| Option | Type | Default | Notes |
|--------|------|---------|-------|
| `art_width` | int 0-648 | `2/3 of h` | Width of the art column, capped at half the box. `0` hides the art |
| `font` | font | `bold-28` | Game name (wraps to 3 lines, then `…`) |
| `show_achievement` | bool | `true` | The "latest achievement" block |

## Data sources

Widgets bind to dotted paths. Unknown paths are rejected when the package loads. A path ending in
`[n]` stands for a list index, e.g. `weather.forecast[0].high`.

| Path | Kind | Notes |
|------|------|-------|
| `time.now` | time | |
| `time.tz` | string | IANA zone name, used by `clock` |
| `system.cpu.load` | number | % |
| `system.cpu.temp` | number | C |
| `system.cpu.clock` | number | MHz |
| `system.gpu.load` | number | % |
| `system.gpu.temp` | number | C |
| `system.gpu.clock` | number | MHz |
| `system.gpu.mem_clock` | number | MHz |
| `system.mem.used_pct` | number | % |
| `system.fan.rpm` | number | |
| `system.power.package_w` | number | W |
| `<each system path above>.history` | series | Last 60 samples, oldest first. For sparklines |
| `steam.now_playing.appid` | number | `steam.now_playing` is `null` when no game runs |
| `steam.now_playing.name` | string | |
| `steam.now_playing.box_art_path` | path | |
| `steam.now_playing.session_minutes` | number | |
| `steam.now_playing.total_minutes` | number | |
| `steam.now_playing.last_achievement` | string | |
| `steam.user.persona` | string | |
| `steam.user.level` | number | |
| `steam.user.games_owned` | number | |
| `steam.user.hours_total` | number | |
| `steam.friends_online` | number | |
| `steam.recent[n].appid` / `.name` / `.minutes_2w` / `.minutes_total` | number / string / number / number | |
| `weather.temp` | number | |
| `weather.condition` | string | |
| `weather.icon` | string | Used with the `icon` widget, see icon names |
| `weather.high`, `weather.low` | number | |
| `weather.units` | string | |
| `weather.sunrise`, `weather.sunset` | time | |
| `weather.forecast[n].date` | time | `n` = 0..2 |
| `weather.forecast[n].high`, `.low` | number | |
| `weather.forecast[n].icon` | string | |
| `settings.<key>` | per setting | From this screen's own `settings` |

Kinds: **number** (int or float, never a bool), **string**, **series** (list of numbers), **path** (a file
path), **time**. Which kind each widget accepts is listed above; using the wrong kind is a load error.
Any value may be `null` or absent: the widget shows `--`.

In a snapshot a path can be written nested (`{"weather": {"temp": 11}}`), as a flat dotted key
(`{"system.cpu.load": 40}`), or mixed. `system.*` values and their `.history` series are siblings, so
write them as flat keys:

```json
{
  "system.cpu.load": 42,
  "system.cpu.load.history": [31, 38, 44, 42],
  "steam.now_playing": {"name": "Hades", "box_art_path": "art.png", "session_minutes": 83}
}
```

## Settings

`settings` declares options the user can change per screen. They are available to bindings as
`settings.<key>`.

| Field | Type | Notes |
|-------|------|-------|
| `key` | string | `a-z`, `0-9`, `_`, starts with a letter, unique |
| `type` | `string` `number` `bool` `enum` | |
| `default` | matching type | Required. Must satisfy the rules below |
| `label` | string | Shown in the settings page |
| `min`, `max` | number | `number` only |
| `values` | array of strings | `enum` only: non-empty, unique |

User values are checked against the same rules; unknown keys and wrong types are errors.

## Code tier

`active.code` may point to a `screen.py` (path inside the package, `.py`, at most 256 KiB). The engine
validates that the file exists and stays inside the package and exposes it as `Screen.code_path`; the
declarative renderer never runs it. A code-tier screen is drawn by `screen.py` instead of the widgets and only
runs after the user approves it, in a separate guarded process. How to write, share and install one, and what
the guardrail does and does not protect against: [screen-packages.md](screen-packages.md).

## Idle section

Defined here and **rendered by the panel's firmware**. The plugin compiles it (`inkterface_core/idle/compile.py`) and uploads it
(see [idle-mode.md](idle-mode.md) and the Standby Screens page in [plugin-ui.md](plugin-ui.md)); the host engine itself does not draw it. The binding grammar for idle widgets is not final, so widget options other than geometry
are not validated yet.

| Field | Type | Notes |
|-------|------|-------|
| `render_mode` | `mono` `gray4` | Required. `mono` = 1-bit partial refresh, `gray4` = 4-level grey, full refresh |
| `widgets` | array | Required. Each: `type` in `text number bar icon image clock`, plus `x y w h` (same bounds as active) |
| `refresh_interval_s` | int >= 1 | `gray4` full-refresh interval, default 900 |
| `full_refresh_every` | int >= 1 | `mono`: partial refreshes between full ones, default 60 |
| `sources` | object | Named panel-side sources; each needs `type` in `clock weather steam http_json` |

## Full example

A tiny screen: a title, CPU load as a number and sparkline, a fan bar and a user-set title.
Save as `my-screen/screen.json`:

```json
{
  "schema": 1,
  "id": "my-screen",
  "name": "My Screen",
  "version": "1.0.0",
  "author": "you",
  "description": "CPU load and fan speed.",
  "active": {
    "refresh_s": 1,
    "widgets": [
      { "type": "text", "x": 0, "y": 0, "w": 648, "h": 30, "text": " {settings.title}",
        "font": "bold-20", "valign": "middle", "invert": true },
      { "type": "number", "x": 8, "y": 40, "w": 160, "h": 56,
        "bind": "system.cpu.load", "unit": "%", "font": "bold-40" },
      { "type": "sparkline", "x": 180, "y": 40, "w": 460, "h": 56, "border": 1,
        "bind": "system.cpu.load.history", "range": [0, 100], "show_labels": true, "fill": true },
      { "type": "icon", "x": 8, "y": 110, "w": 40, "h": 40, "name": "fan" },
      { "type": "bar", "x": 60, "y": 120, "w": 580, "h": 22,
        "bind": "system.fan.rpm", "range": [0, 6000] },
      { "type": "text", "x": 8, "y": 160, "w": 632, "h": 24, "font": "regular-16",
        "text": "Fan {system.fan.rpm:.0f} rpm" }
    ]
  },
  "settings": [
    { "key": "title", "type": "string", "label": "Title", "default": "MY SCREEN" }
  ]
}
```

And `my-screen/sample-data.json`:

```json
{
  "system.cpu.load": 37,
  "system.cpu.load.history": [12, 20, 35, 60, 44, 37],
  "system.fan.rpm": 2800
}
```

Render it:

```bash
python -m inkterface_core.screens.emulate my-screen --out my-screen.png
```

The built-in screens in `decky/screens/` (`telemetry`, `thermals-power`, `now-playing`, `big-clock`) are
complete working examples with `sample-data.json` files.
