# Third-party notices

Inkterface is MIT licensed ([LICENSE](LICENSE)). This file lists every component that is bundled in, fetched into, or
linked with a **release artifact**: the plugin zip (`inkterface-plugin-<version>.zip`) and the EE05 firmware image
(`inkterface-ee05-<version>.bin`). Each component keeps its own licence. Versions are the ones the release build
resolves; `decky/vendor-requirements.txt`, `decky/pnpm-lock.yaml` and `firmware/platformio.ini` are the sources of truth.

Build-time and test-only tools (pytest, pyserial, cryptography, vitest, rollup, PlatformIO and so on) are not shipped and
are not listed.

## Copyleft summary

| Artifact | GPL / AGPL code linked or shipped | LGPL code |
|----------|-----------------------------------|-----------|
| EE05 firmware image | **None.** No GxEPD2, no Adafruit EPD, no NimBLE are linked (checked from the link map of `pio run -e ee05`). GCC's runtime libraries are GPL-3.0 with the GCC Runtime Library Exception, which allows linking into non-GPL programs. | The Arduino-ESP32 core and its bundled libraries are LGPL-2.1-or-later and are linked statically (see below). |
| Plugin zip | None. | `@decky/api` (LGPL-2.1) is bundled into `dist/index.js`. |

GxEPD2 (GPL-3.0) is **not a dependency of any environment** in `firmware/platformio.ini`, and no GxEPD2 code is compiled
in. Older docs said the EE05 firmware used it; it was replaced by the bare-metal driver in `firmware/src/ee05/uc8179_fast.h`.

**Provenance of the controller tables.** The init register sequence and the fast-partial waveform in that driver were
re-derived from the panel datasheet (see [docs/references.md](docs/references.md)); every value is cited, or marked as our own tuning,
in [docs/uc8179-tables.md](docs/uc8179-tables.md), and the bytes on the wire are pinned by a golden test. An earlier
bring-up sketch, which recorded its command sequences as taken from GxEPD2_583_T8, is not part of this repository.

**The 4-gray waveform is not in the repository.** It used to be (`LUT_GRAY`, `LUT_GRAY_BD`: bytes that came, through GxEPD2_4G, from the
Good Display demo of a different 7.5-inch panel). They are removed. The firmware now asks the panel controller to run the 4-gray waveform
stored in its own OTP, the way the panel maker's demo for this panel does: two register writes (`0xE0` = `02`, `0xE5` = `5F`), which are
documented controller commands. No third-party code or table is copied; the origin, the licences of every candidate and the evidence are in
[docs/gray-waveform.md](docs/gray-waveform.md).

## Decky plugin (Python)

| Component | Version | Licence | Where |
|-----------|---------|---------|-------|
| [Pillow](https://python-pillow.github.io/) | 13.0.0 (cp314 manylinux wheel, sha256-pinned) | MIT-CMU (the historical PIL licence, an HPND-style permissive licence) | Fetched into `decky/py_modules/PIL` by `decky/scripts/vendor.sh`, shipped in the zip |
| [certifi](https://github.com/certifi/python-certifi) (Mozilla CA bundle) | 2026.10.23 (py3-none-any wheel, sha256-pinned) | MPL-2.0. The data file `cacert.pem` is shipped unmodified; the licence text is in `py_modules/certifi-2026.10.23.dist-info/licenses/LICENSE` inside the zip, and the source is the public certifi repository. | Fetched into `decky/py_modules/certifi` by `decky/scripts/vendor.sh`, shipped in the zip. Last-resort TLS trust store, read by path in `inkterface_core/tls.py` when the OS has no CA bundle ([docs/troubleshooting.md](docs/troubleshooting.md)). |
| Pillow's bundled native libraries: libavif (with AOM, dav1d), Brotli, bzip2, FreeType, HarfBuzz, Little CMS 2, libjpeg-turbo, libpng, libtiff, libwebp, OpenJPEG, xz (liblzma), Zstandard, libxcb, libXau | as built into the wheel (`pillow.libs/`) | Permissive (BSD, MIT, FreeType License, libpng, IJG, zlib). FreeType and Zstandard are dual-licensed with GPL options; they are used under the FreeType License and the BSD licence. | Shipped in the zip with the wheel. All the licence texts are in `py_modules/pillow-13.0.0.dist-info/licenses/LICENSE` inside the zip. |
| [IBM Plex Mono](https://github.com/IBM/plex) 2.3 Regular and Bold, **modified as "Inkterface Mono"** | as committed | SIL Open Font License 1.1 (`decky/py_modules/inkterface_core/screens/fonts/OFL.txt`, copyright IBM Corp., Reserved Font Name "Plex") | `screens/fonts/InkterfaceMono-*.ttf`, shipped in the zip. The OFL reserves the name "Plex", so the modified fonts are renamed; the change (TrueType hinting instructions removed, nothing else) is in `screens/fonts/FONTLOG.txt` and made by `decky/scripts/make_inkterface_mono.py` from the unmodified originals in `decky/fonts-src/` (not shipped in the zip). The same glyphs, rasterised to 1-bit bitmaps, are compiled into the firmware (below). |
| Ed25519 verifier | n/a | MIT (this project); follows RFC 8032 (copyright IETF Trust; code components of RFCs are Revised BSD) | `ota/ed25519.py`: a pure-Python implementation in the style of the RFC 8032 section 6 reference code (the Decky bundle has no crypto package). It is tested against the RFC 8032 section 7.1 vectors and, in CI, against the `cryptography` package. It is not a copy of a library; the RFC attribution is given in case any reference-code structure was adapted. |
| Decky plugin template | n/a | BSD-3-Clause, full text at the end of this file (copyright "Steam Deck Homebrew") | Template code that is still in the tree: `decky/rollup.config.js` (verbatim), `decky/tsconfig.json` and the `build`/`watch` scripts and `plugin.json` skeleton (adapted). None of it is in `dist/index.js`; the zip carries the adapted `plugin.json` and `package.json` only. `decky/LICENSE` is the project's MIT text since 1.0.0-beta.1; the template's own `LICENSE` (BSD-3, with a placeholder "Hypothetical Plugin Developer" line) is not shipped, and its notice is kept below instead. |

## Decky plugin (frontend, bundled into `dist/index.js`)

Found from the source map of `pnpm build` (everything else in the bundle is this project's code):

| Component | Version | Licence | Notes |
|-----------|---------|---------|-------|
| [`@decky/api`](https://github.com/SteamDeckHomebrew/decky-frontend-lib) | 1.1.3 | LGPL-2.1 | Bundled. It is the Decky Loader client library every plugin ships with. Its source is public. |
| [`react-icons`](https://github.com/react-icons/react-icons) (core + the `fa` set) | 5.3.0 | MIT for the package | Nine icons are used (`FaArrowDown`, `FaArrowUp`, `FaChevronRight`, `FaCode`, `FaPlay`, `FaPlus`, `FaSyncAlt`, `FaThumbtack`, `FaTimes`), from Font Awesome Free 5. The plugin's own icon is Valve's pixel-art Inkterface icon, not a Font Awesome glyph. Font Awesome Free icons are CC BY 4.0 (https://fontawesome.com/license/free, attribution given here). |

Not bundled: `@decky/ui` (LGPL-2.1), React and `tslib` are build-time or provided by the Steam client and Decky Loader at
runtime.

## EE05 firmware (linked into the release image)

| Component | Version | Licence | Notes |
|-----------|---------|---------|-------|
| [ArduinoJson](https://arduinojson.org/) | 7.4.3 (pinned in `firmware/platformio.ini`) | MIT | Copyright Benoit Blanchon. |
| [Arduino-ESP32](https://github.com/espressif/arduino-esp32) core, with the `WiFi`, `WiFiClientSecure`, `HTTPClient`, `Preferences` and `SPI` libraries | 2.0.17 (PlatformIO `framework-arduinoespressif32` 4.20017) | LGPL-2.1-or-later | Linked statically. LGPL asks that a recipient can rebuild with a modified copy of the library: this repository is the complete source and `pio run -e ee05` is the build, which satisfies that. |
| [ESP-IDF](https://github.com/espressif/esp-idf) components shipped as libraries inside Arduino-ESP32 (FreeRTOS, lwIP, mbedTLS, wpa_supplicant, newlib glue, drivers, OTA and partition code) | the version inside Arduino-ESP32 2.0.17 | Apache-2.0 overall; individual parts are MIT or BSD (FreeRTOS MIT, lwIP and wpa_supplicant BSD-3-Clause, mbedTLS Apache-2.0) | Copyright Espressif Systems and others. |
| Espressif binary libraries (Wi-Fi, PHY, coexistence) | inside Arduino-ESP32 2.0.17 | Distributed by Espressif as binaries for use on Espressif chips, which is what this is | No source is available for them. |
| newlib (C library) and the GCC runtime (`libgcc`, `libstdc++`) from `toolchain-xtensa-esp32s3` 8.4.0 | as in the toolchain | newlib: BSD-style and other permissive licences. GCC runtime: GPL-3.0-or-later with the GCC Runtime Library Exception | The exception covers the compiled output of a non-GPL program. |
| Inkterface Mono (IBM Plex Mono) glyph bitmaps | as committed | SIL OFL 1.1 | `firmware/src/ee05/idle_fonts_data.cpp` is generated by `firmware/scripts/gen_idle_fonts.py` from the Inkterface Mono TTFs above. Embedding is allowed by the OFL; the bitmaps are not distributed as a font. |

## Not in this repository

The original Feather + BLE firmware (Adafruit EPD, Adafruit MAX1704X, Adafruit GFX, NimBLE-Arduino), the Qt app, the
Adafruit board files and the vendor datasheets used to be tracked here. They are not any more; nothing in a release
built from this tree uses them. Valve's repository still has them (see [docs/references.md](docs/references.md)). The
datasheets and the Adafruit board files remain the property of their authors; check their terms before redistributing.
The enclosure is a remix of Valve's case (Valve's original design is MIT, see below). The remix is published on Printables under CC BY-NC-SA 4.0 and is not part of this repository; the code here stays MIT.

## Original work

The original Inkterface project (Bluetooth app, Feather firmware, CAD and hardware design) is copyright Valve Software
and MIT licensed. The USB protocol, Decky plugin, EE05 firmware, release tooling and documentation added since are
copyright FliesWithWind, under the same licence.

## Decky plugin template licence (BSD 3-Clause)

Applies to the template files listed in the Decky plugin table above.

```
BSD 3-Clause License

Copyright (c) 2022-2024, Steam Deck Homebrew

All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its
   contributors may be used to endorse or promote products derived from
   this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```
