# Using Inkterface

## Serial permission

The plugin does not ask for root. Decky runs a plugin without the `root` flag as user `deck`
(`setuid`, no supplementary groups, so `uucp`/`dialout` membership does not help). On SteamOS
3.9.5 this works with no setup: systemd tags the Espressif USB-Serial-JTAG port `uaccess`, so
the logged-in user gets an ACL on `/dev/ttyACM*` (`getfacl` shows `user:deck:rw-`). This was
verified on a real Steam Machine.

If the status line shows `Permission denied`, the ACL is missing (for example no user is logged
in on the seat). Add a udev rule once (needs root; the rule is the same idiom SteamOS ships for
Steam VR hardware, but it was not run on the test machine, which needed none):

```
# /etc/udev/rules.d/99-inkterface.rules
SUBSYSTEM=="tty", ATTRS{idVendor}=="303a", ATTRS{idProduct}=="1001", TAG+="uaccess", OWNER="deck"
```

then `sudo udevadm control --reload && sudo udevadm trigger` and replug the panel. Undo: delete the file.
Do not make the plugin `root` to work around this: a root plugin runs imported code screens as root.

The plugin finds the panel by its USB vendor id (Espressif `303a`), so it never opens other
serial devices such as game controllers.

## Using screens

Open **Inkterface** from the Decky quick-access panel for the live preview, or open the full
settings pages from there.

* **Rotation.** The panel cycles through the enabled screens. Set seconds per screen (5 to 300)
  and the order on the **Live Screens** page (chevrons, L1/R1 or X on a controller).
* **Pin.** Pick one screen in the quick-access panel's **Pin screen** dropdown to keep it up.
  Choose `Auto (rotation)` to go back to cycling.
* **In a game.** With *Different screens while a game is running* on, the Live Screens page has a second list
  for when a game is running, with its own order, timing and pin.
  With it off, the panel switches to the `now-playing` screen (box art, title, playtime) when a game starts and
  returns to the rotation when it exits. A pinned screen wins over both.
* **Screen settings.** Each screen can declare settings (time format, units, titles), shown as
  a form on its card.
* **Standby screens.** When the machine is off (or the plugin stops), the panel hands over to its
  own standby screens: clock, weather, Steam stats. These are drawn by the firmware, and Wi-Fi,
  location and Steam details are configured from the plugin's **Standby Screens** and **Settings** pages. The
  authoring format is in [screen-format.md](screen-format.md#idle-section). The
  plugin disables the Standby Screens controls on a panel whose firmware does not report standby support.
* **Sleep and wake.** When the Steam Machine goes to sleep the panel switches to its standby
  screens right away (the plugin listens to systemd-logind and holds a short delay lock; the
  firmware also notices a USB bus that went quiet within ~2 s, in case the plugin is not
  running), and the active screen is back a few seconds after the resume. Details and a
  one-minute check in [idle-mode.md](idle-mode.md#sleep-and-wake).

Built-in screens: `telemetry`, `thermals-power`, `big-clock`, `now-playing` (active, drawn by the
plugin) and `clock-weather`, `clock-weather-gray4`, `minimal-clock`, `steam-stats` (idle, drawn by the panel).

## Firmware updates

The plugin checks GitHub releases for a newer `fw-v*` firmware once a day. It never installs
by itself: the **Firmware** page shows the changelog and an Install button, then streams the
image to the panel over USB. If the new firmware does not confirm within ~3 minutes the panel
rolls back to the previous one automatically. Flow, trust model and key rotation: [ota.md](ota.md).
