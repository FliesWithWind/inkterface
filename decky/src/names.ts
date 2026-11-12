// The user-facing page names and the sentences that point at them, in one place. Internal ids, routes and setting keys
// (`/inkterface/idle`, `idle_screens`, `idle_timeout_s`) keep their old names on purpose: they are not shown to anyone.
export const NAMES = {
  live: "Live Screens",
  standby: "Standby Screens",
  store: "Screen Store",
  firmware: "Firmware",
  settings: "Settings",
  about: "About",
} as const;

/** Sidebar order. */
export const SIDEBAR = [NAMES.live, NAMES.standby, NAMES.store, NAMES.firmware, NAMES.settings, NAMES.about] as const;

export const LIVE_EMPTY = `No screens are installed. Add one in the ${NAMES.store}.`;
export const LIVE_STANDBY_NOTE = (n: number) =>
  `${n} ${n === 1 ? "screen is" : "screens are"} drawn by the panel itself while the machine is off. Choose ${n === 1 ? "it" : "them"} on the ${NAMES.standby} page.`;
export const LIVE_STANDBY_LINK = `Standby screens are drawn by the panel itself while the machine is off. Choose them on the ${NAMES.standby} page.`;
export const APPROVAL_WAITING = `Waiting for your approval in the ${NAMES.store} (Installed) before it can run.`;

export const STANDBY_NO_LAYOUT = "No standby layout";
export const STANDBY_DRAWN_BY_PANEL = "Standby screen: drawn by the panel";
export const STANDBY_UNSUPPORTED =
  `This panel's firmware has no standby storage, so it cannot show standby screens yet. Update it on the ${NAMES.firmware} page. ` +
  "Choices you make now are saved and sent once the firmware supports them.";
export const STANDBY_OFFLINE = "The panel is not connected. Your choices are saved and sent to it when it connects.";
export const STANDBY_SENDING = "Sending standby screens to the panel…";
export const STANDBY_ON_PANEL = "Standby screens are on the panel";
export const STANDBY_NOT_SENT = "Standby screens not sent";
export const STANDBY_FW_CANNOT = "Panel firmware cannot show standby screens";
export const STANDBY_READY = "Ready for standby screens.";
export const STANDBY_TIMEOUT_LABEL = "Standby timeout (seconds)";
export const STANDBY_TIMEOUT_DESC = "How long before the panel switches to its own standby screens.";
export const STANDBY_WIFI_NOTE = "standby screens while the machine is off";

// Screen Store page ("not set up" friendly empty state, shown when no catalog signing key is bundled in the build).
export const STORE_NOT_SET_UP_TITLE = `The ${NAMES.store} is not set up yet`;
export const STORE_NOT_SET_UP_BODY =
  "Community screens will appear here once the catalog is switched on. In the meantime you can import your own on the " +
  "Installed tab with Developer mode, or set a catalog in Settings → Screen catalog.";

export const STORE_REINSTALL_NOTE = `The screen and its settings are removed. You can install it again from the ${NAMES.store}.`;
export const DEV_MODE_WARNING =
  `Lets you import screen packages from a .zip or a link under ${NAMES.store} → Installed. Those are not reviewed by anyone: ` +
  "you are trusting the author. Code screens still ask for your approval before they run, but from here on you are on your own.";
export const CATALOG_URL_NOTE = `Where the ${NAMES.store} downloads its list of screens from. Leave it empty to use the default. Signed with the bundled catalog key.`;
