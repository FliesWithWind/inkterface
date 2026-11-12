# First-time firmware flash

A new EE05 ships with its stock firmware. The first flash of Inkterface firmware is done once
over USB from a computer with [PlatformIO](https://platformio.org). It also installs the
bootloader with rollback support and the dual-slot partition table that firmware updates need.
After that, updates come through the plugin ([ota.md](ota.md)). (The `inkterface-ee05-<version>.bin` on a firmware release is the
app image for those updates, not a complete flash image, so it cannot replace this first flash.)

1. **Back up the panel first.** The backup contains Wi-Fi and Steam settings stored on the
   board; keep it private and never commit it. It is the full 16 MB flash:

   ```sh
   cd firmware
   pio pkg exec -p tool-esptoolpy -- esptool.py --chip esp32s3 --port <PORT> --baud 921600 \
       read_flash 0 ALL ~/ee05-full-$(date +%Y%m%d).bin
   ```

   `<PORT>` is the panel's `/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_*-if00`
   entry. On a machine with game controllers, never guess `/dev/ttyACM0` or `ttyACM1`: use the
   by-id path or `python3 decky/tools/send_test.py --list`, which identifies the panel by USB
   VID.

2. **Flash.** The build needs a local config header; the public example is enough, Wi-Fi and
   Steam secrets are not compiled into the image:

   ```sh
   cd firmware
   cp -n src/ee05/idle_config.h.example src/ee05/idle_config.h
   pio run -e ee05 -t upload --upload-port <PORT>
   ```

   With the example config the panel cannot join Wi-Fi, so it takes ~25 s after reset before
   USB answers.

3. **Restore from backup** (whole flash, including NVS and the original firmware, ~4 min):

   ```sh
   pio pkg exec -p tool-esptoolpy -- esptool.py --chip esp32s3 --port <PORT> --baud 921600 \
       write_flash 0 <backup.bin>
   ```

More detail and the optional hardware test run are in the "Hardware test procedure" section of
[usb-protocol-v1.md](usb-protocol-v1.md).
