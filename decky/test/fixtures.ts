import { CatalogEntry, FirmwareStatus, ScreenInfo, Settings, Status } from "../src/api";
import { __mock } from "./mocks/decky-api";

export const status = (o: Partial<Status> = {}): Status => ({
  ui_state: "connected", state: "ready", connected: true, fw_version: "1.2.3", protocol_version: 1, panel_size: [648, 480],
  current_screen: "telemetry", pinned: null, now_playing: null, profile: "normal", last_error: null, last_frame_at: 100, frames_rendered: 5,
  retry_in_s: null, idle_supported: false,
  idle: { state: "none", error: null, ids: [], on_panel: [], modes: [], bytes: 0, free: null, timeout_s: 60 }, plugin_version: "0.0.1", host_error: null, screen_errors: [], ...o,
});

export const screen = (id: string, o: Partial<ScreenInfo> = {}): ScreenInfo => ({
  id, name: id[0].toUpperCase() + id.slice(1), version: "1.0.0", author: "Inkterface", description: "", builtin: true, enabled: true,
  has_code: false, code_sha256: null, approved: true, needs_approval: false, has_active: true, has_idle: false, refresh_s: 1,
  settings_schema: [], settings: {}, current: false, pinned: false, in_rotation: false, in_idle: false, ...o,
});

export const settings = (o: Partial<Settings> = {}): Settings => ({
  rotation: ["telemetry", "clock"], rotation_interval_s: 30, pinned: null, auto_now_playing: true, now_playing_screen: "now-playing",
  idle_timeout_s: 60, ghost_clear_minutes: 10, screen_settings: {}, ingame_enabled: false, ingame: null, weather_lat: null, weather_lon: null, weather_place: "", units: "metric", steamid64: "",
  steam_api_key: "unset", idle_screens: [], idle_modes: {}, idle_rotate_s: 60, wifi_ssid: "", wifi_pass: "unset", purge_on_uninstall: false,
  developer_mode: false, catalog_url: "", catalog_url_default: "https://flieswithwind.github.io/inkterface-screens/", errors: [], ...o,
});

export const catalogEntry = (id: string, o: Partial<CatalogEntry> = {}): CatalogEntry => ({
  id, name: id[0].toUpperCase() + id.slice(1), version: "1.0.0", author: "Ada", description: "", has_code: false, has_active: true, has_idle: false,
  size: 2048, min_plugin_version: "0.0.1", installed: false, installed_version: null, update_available: false, needs_approval: false,
  compatible: true, incompatible_reason: null, ...o,
});

export const firmware = (o: Partial<FirmwareStatus> = {}): FirmwareStatus => ({
  installed: { version: "1.0.0", dev_build: false, protocol_version: 1, connected: true, seen: true },
  latest: { version: "1.2.0", tag: "fw-v1.2.0", changelog: "- Faster refresh\n- Fixes", size: 1480400, page_url: "https://github.com/FliesWithWind/inkterface/releases/tag/fw-v1.2.0" },
  update_available: true, note: null, trust: { state: "ok", message: null },
  check: { at: 1_700_000_000, ok: true, error: null, error_kind: null, next_at: 1_700_086_400, checking: false, retry_at: null, limit_message: null },
  protocol_mismatch: false, mismatch_message: null,
  install: { state: "idle", version: null, done: 0, total: 0, percent: 0, message: null, error: null, cancellable: false, rollback_pending: false },
  ...o,
});

export const installing = (state: FirmwareStatus["install"]["state"], o: Partial<FirmwareStatus["install"]> = {}): FirmwareStatus["install"] => ({
  state, version: "1.2.0", done: 0, total: 1000, percent: 0, message: null, error: null, cancellable: true, rollback_pending: false, ...o,
});

export const PNG = "data:image/png;base64,iVBORw0KGgo=";
export const OK = { ok: true };

/** Registers a happy-path backend; individual tests override single callables with __mock.on(). */
export function backend(o: { status?: Partial<Status>; screens?: ScreenInfo[]; settings?: Partial<Settings>; firmware?: Partial<FirmwareStatus> } = {}) {
  const screens = o.screens ?? [screen("telemetry", { current: true, in_rotation: true }), screen("clock", { in_rotation: true })];
  __mock.on("get_status", () => status(o.status));
  __mock.on("list_screens", () => screens);
  __mock.on("get_settings", () => settings(o.settings));
  __mock.on("get_firmware_status", () => firmware(o.firmware));
  __mock.on("check_firmware", () => firmware(o.firmware));
  __mock.on("install_firmware", () => OK);
  __mock.on("cancel_firmware_install", () => OK);
  __mock.on("preview_screen", () => ({ ok: true, data_url: PNG }));
  __mock.on("preview_idle_screen", () => ({ ok: true, data_url: PNG }));
  for (const n of ["set_settings", "set_screen_settings", "pin_screen", "unpin_screen", "set_rotation", "set_screen_enabled",
    "set_now_playing", "approve_package", "uninstall_package"]) __mock.on(n, () => OK);
  __mock.on("push_idle_layouts", () => ({ ok: false, error: "this firmware has no idle storage: update the firmware", supported: false }));
  // Settings > Connections helpers: refuse by default, so a test that forgets to register one fails loudly instead of seeing a half answer
  for (const n of ["search_places", "locate_by_ip", "resolve_steam_id", "steam_profile", "test_steam_key", "wifi_current"]) __mock.on(n, () => ({ ok: false, error: `${n}: not mocked` }));
  // Catalog: an empty, saved, reachable catalog by default; Store tests override these.
  __mock.on("catalog_list", () => ({ ok: true, entries: [], fetched_at: 1_700_000_000, url: settings(o.settings).catalog_url_default }));
  __mock.on("catalog_refresh", () => ({ ok: true, fetched_at: 1_700_000_000, count: 0 }));
  __mock.on("catalog_updates", () => ({ ok: true, updates: [] }));
  __mock.on("catalog_install", () => ({ ok: true, needs_approval: false, package: screens[0] ?? screen("x", { builtin: false }) }));
  __mock.on("catalog_preview", () => ({ ok: true, data_url: PNG }));
  return screens;
}
