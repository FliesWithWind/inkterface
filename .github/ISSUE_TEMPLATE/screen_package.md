---
name: Screen package
about: Share a community screen, or report a problem with one
title: "Screen: "
labels: screen
assignees: ""
---

**Screen name, id and version** (from `screen.json`)

**What it shows** (a rendered PNG from the emulator helps most)

```sh
cd decky/py_modules
python -m inkterface_core.screens.emulate path/to/my-screen --out my-screen.png
```

**Package** (link to the zip or the repository; https only)

**Type**
- [ ] Declarative only (`screen.json`)
- [ ] Includes code (`screen.py`). Code screens run with the plugin's permissions; say what the code does and what it reads.

**Checks**
- [ ] The folder name equals the `id` and `sample-data.json` is included
- [ ] It renders with the emulator and imports with **Install from file** in the plugin
- [ ] It uses only the standard library and Pillow
- [ ] I have the right to share everything in it (fonts, images, data) and I state its licence below

**Licence of the screen**

**Problem report** (leave empty when you are sharing a screen): what you did, what you saw, plugin version.

Format: [docs/screen-format.md](../../docs/screen-format.md). Sharing and approval: [docs/screen-packages.md](../../docs/screen-packages.md).
