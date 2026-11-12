// Typed wrappers around the Python callables in main.py. See docs/plugin-ui.md.
import { addEventListener, callable, removeEventListener } from "@decky/api";

export type UiState = "connected" | "connecting" | "waiting" | "firmware_too_old" | "updating" | "stopped";

export type IdleMode = "mono" | "gray4";
export type IdleState = "none" | "pending" | "uploading" | "ok" | "error" | "unsupported";

/** Which idle layouts are on the panel and how the last upload went. Never carries the layouts or any secret. */
export interface IdleStatus {
  state: IdleState;
  error: string | null;
  ids: string[]; // screens the plugin wants on the panel, in order
  on_panel: string[]; // screens in the last store the panel acknowledged
  modes: IdleMode[];
  bytes: number;
  free: number | null; // store capacity the panel reported
  timeout_s: number | null;
}

export interface Status {
  ui_state: UiState;
  state: string;
  connected: boolean;
  fw_version: string | null;
  protocol_version: number | null;
  panel_size: [number, number] | null;
  current_screen: string | null;
  pinned: string | null; // the pin of the profile in use
  now_playing: number | null;
  profile: Profile; // which rotation / pin the engine uses right now
  last_error: string | null;
  last_frame_at: number | null;
  frames_rendered: number;
  retry_in_s: number | null;
  idle_supported: boolean;
  idle: IdleStatus;
  plugin_version: string;
  host_error: string | null;
  screen_errors: string[];
}

export type InstallState = "idle" | "downloading" | "preparing" | "sending" | "confirming" | "done" | "failed" | "cancelled";

/** get_firmware_status, also pushed by the `firmware` event. See docs/ota.md and docs/plugin-ui.md. */
export interface FirmwareStatus {
  installed: { version: string | null; dev_build: boolean; protocol_version: number | null; connected: boolean; seen: boolean };
  latest: { version: string; tag: string; changelog: string; size: number; page_url: string } | null;
  update_available: boolean;
  note: string | null;
  trust: { state: "ok" | "no_key"; message: string | null };
  check: { at: number | null; ok: boolean | null; error: string | null; error_kind: string | null; next_at: number | null; checking: boolean;
    /** GitHub rate limit: unix time it lifts (null = unknown) and the fixed explanation; both null when not limited. */
    retry_at?: number | null; limit_message?: string | null };
  protocol_mismatch: boolean;
  mismatch_message: string | null;
  install: {
    state: InstallState;
    version: string | null;
    done: number;
    total: number;
    percent: number;
    message: string | null;
    error: string | null;
    cancellable: boolean;
    rollback_pending: boolean;
  };
}

export type SettingType = "bool" | "number" | "string" | "enum";
export interface SettingDef {
  key: string;
  type: SettingType;
  label?: string;
  default: boolean | number | string;
  min?: number;
  max?: number;
  values?: string[];
}
export type SettingValue = boolean | number | string;

export interface ScreenInfo {
  id: string;
  name: string;
  version: string;
  author: string;
  description: string;
  builtin: boolean;
  enabled: boolean;
  has_code: boolean;
  code_sha256: string | null;
  approved: boolean;
  needs_approval: boolean;
  has_active: boolean;
  has_idle: boolean;
  refresh_s: number | null;
  settings_schema: SettingDef[];
  settings: Record<string, SettingValue>;
  current: boolean;
  pinned: boolean;
  in_rotation: boolean;
  in_idle: boolean;
  idle_modes?: IdleMode[]; // render modes the idle layout compiles in (only with has_idle)
  idle_mode?: IdleMode; // the one that would be uploaded
  error?: string;
}

/** One screen in the signed catalog, as the backend reports it to the UI (zip/sha/preview paths are not sent). */
export interface CatalogEntry {
  id: string;
  name: string;
  version: string;
  author: string;
  description: string;
  has_code: boolean;
  has_active: boolean;
  has_idle: boolean;
  size: number;
  min_plugin_version: string;
  installed: boolean;
  installed_version: string | null;
  update_available: boolean;
  needs_approval: boolean; // installed code screen that is not approved
  compatible: boolean;
  incompatible_reason: string | null;
}

export interface CatalogList {
  entries: CatalogEntry[];
  fetched_at: number | null; // unix seconds the cache was saved, null when nothing is saved yet
  url: string;
}

export interface CatalogRefresh {
  fetched_at: number;
  count: number;
}

export interface CatalogUpdate {
  id: string;
  name: string;
  installed_version: string;
  version: string;
}

/** "normal" = not in a game (rotation / rotation_interval_s / pinned); "ingame" = Settings.ingame, used while a game runs and ingame_enabled is on. */
export type Profile = "normal" | "ingame";
export interface RotationProfile { rotation: string[]; rotation_interval_s: number; pinned: string | null }

export interface Settings {
  rotation: string[];
  rotation_interval_s: number;
  pinned: string | null;
  auto_now_playing: boolean;
  now_playing_screen: string;
  idle_timeout_s: number;
  ghost_clear_minutes: number; // minutes between full refreshes that clear ghosting, 0 = never
  screen_settings: Record<string, Record<string, SettingValue>>;
  ingame_enabled: boolean; // a second rotation + pin while a game runs (off = one list, as before)
  ingame: RotationProfile | null; // null until first enabled; then a copy of the normal profile
  weather_lat: number | null;
  weather_lon: number | null;
  weather_place: string; // the name the user picked ("Kraków, Poland"); "" for raw coordinates
  units: "metric" | "imperial";
  steamid64: string;
  steam_api_key: "set" | "unset"; // the key itself is never sent to the UI
  idle_screens: string[]; // shown in this order
  idle_modes: Record<string, IdleMode>;
  idle_rotate_s: number; // seconds per idle screen, 0 = stay on the first
  wifi_ssid: string;
  wifi_pass: "set" | "unset"; // never sent to the UI
  purge_on_uninstall: boolean;
  developer_mode: boolean; // zip/URL import is a developer feature; off by default
  catalog_url: string; // "" = use catalog_url_default
  catalog_url_default: string; // read-only, the bundled default base URL
  errors: string[];
}

export type Result<T = unknown> = ({ ok: true } & T) | { ok: false; error: string };

export const errText = (e: unknown): string => (e instanceof Error ? e.message : String(e));

// Reads reject when the backend is not running; the hooks turn that into an error state.
export const getStatus = callable<[], Status>("get_status");
export const listScreens = callable<[], ScreenInfo[]>("list_screens");
export const getSettings = callable<[], Settings>("get_settings");
export const getFirmwareStatus = callable<[], FirmwareStatus>("get_firmware_status");

// Mutations never reject: a failed call (backend gone, bad input) is { ok: false, error }.
function mutation<A extends unknown[], T = unknown>(name: string) {
  const fn = callable<A, Result<T>>(name);
  return async (...args: A): Promise<Result<T>> => {
    try {
      const r = await fn(...args);
      return r && typeof r === "object" && "ok" in r ? r : { ok: false, error: "unexpected reply from the backend" };
    } catch (e) {
      return { ok: false, error: errText(e) };
    }
  };
}

export type SettingsPatch = Partial<Omit<Settings, "rotation" | "pinned" | "ingame" | "screen_settings" | "errors" | "steam_api_key" | "wifi_pass"> & { steam_api_key: string; wifi_pass: string }>;
export const setSettings = mutation<[SettingsPatch], { settings: Settings }>("set_settings");
export const setScreenSettings = mutation<[string, Record<string, SettingValue>], { settings: Record<string, SettingValue> }>("set_screen_settings");
// `profile` omitted = "normal" (not in a game).
export const pinScreen = mutation<[string, Profile?]>("pin_screen");
export const unpinScreen = mutation<[Profile?]>("unpin_screen");
export const setRotation = mutation<[string[], number | null, Profile?]>("set_rotation");
export const setScreenEnabled = mutation<[string, boolean]>("set_screen_enabled");
export const setNowPlaying = mutation<[number | null]>("set_now_playing");
export const importPackage = mutation<[string], { package: ScreenInfo; needs_approval: boolean }>("import_package");
export const approvePackage = mutation<[string, string, string], { package: ScreenInfo }>("approve_package");
export const uninstallPackage = mutation<[string]>("uninstall_package");
export const previewScreen = mutation<[string], { data_url: string }>("preview_screen");
export const previewIdleScreen = mutation<[string, IdleMode | null], { data_url: string }>("preview_idle_screen");
export const checkFirmware = callable<[], FirmwareStatus>("check_firmware"); // asks GitHub now; rejects only when the backend is down
export const installFirmware = mutation<[string]>("install_firmware");
export const cancelFirmwareInstall = mutation<[]>("cancel_firmware_install");
export const pushIdleLayouts = mutation<[], { supported: boolean; idle: IdleStatus }>("push_idle_layouts");

// Catalog. All return dicts and never raise for bad input (a dead backend becomes { ok: false, error }).
export const catalogRefresh = mutation<[], CatalogRefresh>("catalog_refresh");
export const catalogList = mutation<[], CatalogList>("catalog_list");
export const catalogUpdates = mutation<[], { updates: CatalogUpdate[] }>("catalog_updates");
export const catalogInstall = mutation<[string, string], { package: ScreenInfo; needs_approval: boolean }>("catalog_install");
export const catalogPreview = mutation<[string], { data_url: string }>("catalog_preview");

// Settings > Connections helpers. They read the saved Steam key and SteamID on the backend: the key never travels through the UI.
/** One place from the geocoder (or the IP lookup): `label` tells equal names apart, `name` is what Settings shows once chosen. */
export interface Place {
  label: string;
  name: string;
  lat: number;
  lon: number;
  country_code: string | null;
}
export interface SteamProfile {
  steamid64: string;
  persona: string;
  avatar: string | null; // data URL of Steam's avatar, null when it could not be fetched
  public: boolean;
}
export interface KeyTest {
  persona: string;
  profile_public: boolean;
  games_visible: boolean | null; // null = could not tell
}
export const searchPlaces = mutation<[string, string], { results: Place[] }>("search_places");
export const locateByIp = mutation<[], { name: string; lat: number; lon: number; country_code: string | null; provider: string }>("locate_by_ip");
export const resolveSteamId = mutation<[string], { steamid64: string }>("resolve_steam_id");
export const steamProfile = mutation<[string | null], SteamProfile>("steam_profile");
export const testSteamKey = mutation<[], KeyTest>("test_steam_key");
export const wifiCurrent = mutation<[], { ssid: string | null; saved: string[] }>("wifi_current");

export type EventName = "status" | "screens_changed" | "settings_changed" | "firmware";

/** Subscribe to a backend event; returns the unsubscribe function. */
export function onEvent<A extends unknown[]>(name: EventName, cb: (...args: A) => void): () => void {
  const listener = addEventListener<A>(name, cb);
  return () => removeEventListener(name, listener);
}
