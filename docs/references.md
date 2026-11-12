# References

Third-party documents this project used but does not carry. Valve's links point at commit `477563c`, the
snapshot this project started from, so they keep working if Valve's `main` moves. Valve's repository is
<https://gitlab.steamos.cloud/SteamHardware/SteamMachine/inkterface>.

## Datasheets

| Document | Used for | Where |
|----------|----------|-------|
| 5.83" 648x480 e-paper module `ZJY648480-0583AAAMFGN` (Zhongjingyuan), product specification v3.0, 45 pages, sha256 `f8031dd989c22926625179873225c28d016dc04ffae8847d39d5b01664aa6186` | Every register and LUT value in [uc8179-tables.md](uc8179-tables.md) cites its page numbers | [Valve's copy](https://gitlab.steamos.cloud/SteamHardware/SteamMachine/inkterface/-/blob/477563c/docs/EPD583%20648x480%20Datasheet.pdf). The module maker publishes it only on request; the panel is sold by [Seeed](https://www.seeedstudio.com/5-83-Monochrome-ePaper-Display-with-648x480-Pixels-p-5785.html) |
| ESP32-S3 datasheet | Pins, electrical limits | [espressif.com](https://www.espressif.com/sites/default/files/documentation/esp32-s3_datasheet_en.pdf) |
| ESP32-S3 technical reference manual | USB-Serial-JTAG, SPI, RTC, flash | [espressif.com](https://www.espressif.com/sites/default/files/documentation/esp32-s3_technical_reference_manual_en.pdf) |
| MAX17048 fuel gauge (Feather build only, the EE05 firmware has no battery gauge) | Nothing in v1 | [analog.com](https://www.analog.com/en/products/max17048.html) |
| Adafruit ESP32-S3 Feather pinout (Feather build only) | Nothing in v1 | [adafruit.com/product/5323](https://www.adafruit.com/product/5323) |
| Adafruit ESP32 Feather V2 pinout (Feather build only) | Nothing in v1 | [adafruit.com/product/5400](https://www.adafruit.com/product/5400) |

## Valve's original build (Adafruit Feather + BLE + Qt app)

| Was in this repo | Where it is now |
|------------------|-----------------|
| docs/Inkterface Assembly.mp4, docs/Inkterface Assembly.pdf (assembly guide for the Feather build; the mechanical steps match the EE05 build) | [mp4](https://gitlab.steamos.cloud/SteamHardware/SteamMachine/inkterface/-/blob/477563c/docs/Inkterface%20Assembly.mp4), [pdf](https://gitlab.steamos.cloud/SteamHardware/SteamMachine/inkterface/-/blob/477563c/docs/Inkterface%20Assembly.pdf) |
| docs/Inkterface Setup.pdf (setup of the Qt app) | [pdf](https://gitlab.steamos.cloud/SteamHardware/SteamMachine/inkterface/-/blob/477563c/docs/Inkterface%20Setup.pdf) |
| Feather wiring diagrams and schematics (docs/*.png) | [docs folder](https://gitlab.steamos.cloud/SteamHardware/SteamMachine/inkterface/-/tree/477563c/docs) |
| Feather firmware (firmware/src/feather/, environments featherv1, featherv2) | [firmware folder](https://gitlab.steamos.cloud/SteamHardware/SteamMachine/inkterface/-/tree/477563c/firmware) |
| Qt app (qml/, include/, CMakeLists.txt) | [repository root](https://gitlab.steamos.cloud/SteamHardware/SteamMachine/inkterface/-/tree/477563c) |
| Feather and breakout KiCad projects (boards/) | [boards folder](https://gitlab.steamos.cloud/SteamHardware/SteamMachine/inkterface/-/tree/477563c/boards) |
| UI mockup and board photos (design/) | [design folder](https://gitlab.steamos.cloud/SteamHardware/SteamMachine/inkterface/-/tree/477563c/design) |
| Steam store artwork other than the wordmark (design/store-assets/) | [store-assets folder](https://gitlab.steamos.cloud/SteamHardware/SteamMachine/inkterface/-/tree/477563c/design/store-assets) |


The artwork this project reuses is in [`assets/valve/`](../assets/valve/README.md).
