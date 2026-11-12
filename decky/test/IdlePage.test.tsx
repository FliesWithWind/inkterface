import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { IdleStatus } from "../src/api";
import { wifiStatus } from "../src/components/WifiSection";
import { Navigation } from "@decky/ui";
import { IDLE_PREVIEW_NOTE, IdlePage, uploadLine } from "../src/pages/IdlePage";
import { STANDBY_FW_CANNOT, STANDBY_NOT_SENT, STANDBY_OFFLINE, STANDBY_ON_PANEL, STANDBY_UNSUPPORTED } from "../src/names";
import { PNG, backend, screen as scr, status } from "./fixtures";
import { __mock } from "./mocks/decky-api";

const idleSt = (o: Partial<IdleStatus> = {}): IdleStatus => ({ ...status().idle, ...o });
const screens = (inIdle: string[] = []) => [
  scr("telemetry"),
  scr("weather", { has_idle: true, name: "Weather", description: "Outside now", in_idle: inIdle.includes("weather"), idle_modes: ["mono", "gray4"], idle_mode: "mono" }),
  scr("clockidle", { has_idle: true, has_active: false, name: "Clock idle", in_idle: inIdle.includes("clockidle"), idle_modes: ["mono"], idle_mode: "mono" }),
];
const sent = () => __mock.callsTo("set_settings").map((a) => a[0]);
const supported = (chosen: string[] = [], idle: Partial<IdleStatus> = {}, extra: Parameters<typeof backend>[0] = {}) =>
  backend({ screens: screens(chosen), status: { idle_supported: true, idle: idleSt({ ids: chosen, ...idle }) }, settings: { idle_screens: chosen }, ...extra });

const row = async (name: string) => within(await screen.findByRole("group", { name }));
/** Opens a row's "..." menu (standby: Move up / Move down / Remove from standby screens) and returns the menu. */
const menuOf = async (name: string) => {
  fireEvent.click((await row(name)).getByRole("button", { name: `More for ${name}` }));
  return within(await screen.findByRole("menu"));
};
const removeVia = async (name: string) => fireEvent.click((await menuOf(name)).getByRole("menuitem", { name: "Remove from standby screens" }));
/** Opens a row's Settings sheet (a modal in document.body) and returns the dialog; query it via `screen`, not `container`. */
const sheetOf = async (name: string) => {
  fireEvent.click((await row(name)).getByRole("button", { name: `Settings for ${name}` }));
  return within(await screen.findByRole("dialog", { name }));
};
const add = (name: string) => fireEvent.click(screen.getByRole("button", { name: `Add ${name}` }));
const overview = async () => within(await screen.findByRole("region", { name: "Overview" }));

describe("IdlePage without firmware support", () => {
  it("explains, keeps the choices editable and blocks only the upload", async () => {
    backend({ screens: screens(), status: { idle_supported: false, idle: idleSt({ state: "unsupported", error: "no idle storage" }) } });
    render(<IdlePage />);
    expect(await screen.findByText(STANDBY_UNSUPPORTED)).toBeInTheDocument();
    expect(screen.getByText(STANDBY_FW_CANNOT)).toBeInTheDocument(); // "Panel firmware cannot show standby screens"
    expect(screen.getByRole("button", { name: "Add Weather" })).not.toBeDisabled();
    expect(screen.getByText("Send standby screens to the panel")).toBeDisabled();
    expect(screen.getByText("Outside now")).toBeInTheDocument();
    expect(screen.queryByText("Telemetry")).toBeNull(); // only screens with a standby layout
  });

  it("says so when no installed screen has a standby layout", async () => {
    backend({ screens: [scr("telemetry")] });
    render(<IdlePage />);
    expect(await screen.findByText("No installed screen has a standby layout.")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Available standby screens" })).toBeNull();
    expect(screen.queryByText(/Nothing selected yet/)).toBeNull();
  });
});

describe("IdlePage with the panel offline", () => {
  it("lets you choose, says they are sent on connect, and disables the manual push", async () => {
    backend({ screens: screens(["weather"]), settings: { idle_screens: ["weather"] }, status: { connected: false, ui_state: "waiting", idle_supported: false, idle: idleSt({ state: "pending", ids: ["weather"] }) } });
    render(<IdlePage />);
    expect(await screen.findByText(STANDBY_OFFLINE)).toBeInTheDocument();
    expect(screen.getByText("Panel not connected")).toBeInTheDocument();
    expect(screen.queryByText(STANDBY_UNSUPPORTED)).toBeNull();
    expect(screen.getByRole("button", { name: "Add Clock idle" })).not.toBeDisabled();
    expect(screen.getByText("Waiting for the panel to connect…")).toBeInTheDocument();
    expect(screen.getByText("Send standby screens to the panel")).toBeDisabled();
  });
});

describe("IdlePage Overview card", () => {
  it("states the firmware (as the detail), the Wi-Fi and (once uploaded) the storage as facts", async () => {
    supported(["weather"], { state: "ok", on_panel: ["weather"], bytes: 12288, free: 49152 }, { settings: { wifi_ssid: "HomeNet", wifi_pass: "set" } });
    render(<IdlePage />);
    const o = await overview();
    expect(o.getByText("Firmware 1.2.3")).toBeInTheDocument(); // firmware is the panel detail line, not a fact
    const fact = (label: string) => o.getByText(label).closest("div")!;
    expect(within(fact("Wi-Fi")).getByText("HomeNet")).toBeInTheDocument();
    expect(within(fact("Storage used")).getByText("25%")).toBeInTheDocument();
  });

  it("says the Wi-Fi is not set when unknown, and omits the storage fact before an upload", async () => {
    supported([], {}, { settings: { wifi_ssid: "", wifi_pass: "unset" } });
    render(<IdlePage />);
    const o = await overview();
    expect(within(o.getByText("Wi-Fi").closest("div")!).getByText("Not set")).toBeInTheDocument();
    expect(o.queryByText("Storage used")).toBeNull(); // nothing on the panel yet
  });

  it("reports the rotation as a fact only once more than one screen is chosen", async () => {
    backend({ screens: screens(["weather", "clockidle"]), settings: { idle_screens: ["weather", "clockidle"], idle_rotate_s: 45 }, status: { idle_supported: true } });
    render(<IdlePage />);
    expect(within((await overview()).getByText("Changes").closest("div")!).getByText("every 45 seconds")).toBeInTheDocument();
  });
});

describe("IdlePage choosing screens", () => {
  it("says the panel is ready, offers the list, and has nothing to send yet", async () => {
    supported();
    render(<IdlePage />);
    expect(await screen.findByText("Panel connected")).toBeInTheDocument();
    expect(screen.getByText("Firmware 1.2.3")).toBeInTheDocument();
    expect(screen.queryByText(STANDBY_UNSUPPORTED)).toBeNull();
    expect(screen.queryByText(STANDBY_OFFLINE)).toBeNull();
    expect(screen.getByText("Nothing selected yet. Add a screen from the list below.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add Weather" })).not.toBeDisabled();
    expect(screen.getByText("Send standby screens to the panel")).toBeDisabled();
    expect(screen.getByText(IDLE_PREVIEW_NOTE)).toBeInTheDocument();
  });

  it("adds a screen at the end and removes one through the row menu", async () => {
    supported(["weather"]);
    render(<IdlePage />);
    await row("Weather");
    expect(screen.queryByRole("button", { name: "Add Weather" })).toBeNull(); // chosen ones leave the available list
    add("Clock idle");
    await waitFor(() => expect(sent()).toEqual([{ idle_screens: ["weather", "clockidle"] }]));
    await removeVia("Weather");
    await waitFor(() => expect(sent()[1]).toEqual({ idle_screens: [] })); // the mocked settings are static, so this removes the only chosen one
  });

  it("lists chosen screens in the saved order, numbered, and moves them through track and menu", async () => {
    backend({ screens: screens(["weather", "clockidle"]), settings: { idle_screens: ["clockidle", "weather"] }, status: { idle_supported: true } });
    render(<IdlePage />);
    await row("Weather");
    const order = screen.getAllByRole("group").filter((g) => g.hasAttribute("data-row-id")).map((g) => g.getAttribute("aria-label"));
    expect(order).toEqual(["Clock idle", "Weather"]);
    expect((await row("Clock idle")).getByRole("group", { name: "Position 1 of 2" })).toHaveTextContent("1");
    expect((await row("Weather")).getByRole("group", { name: "Position 2 of 2" })).toHaveTextContent("2");
    expect(screen.getByText("Order: Clock idle → Weather. It changes every 1 minute.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Move Clock idle up to position 0" })).toBeDisabled(); // first: no up
    expect(screen.getByRole("button", { name: "Move Weather down to position 3" })).toBeDisabled(); // last: no down
    fireEvent.click(screen.getByRole("button", { name: "Move Weather up to position 1" }));
    await waitFor(() => expect(sent()).toEqual([{ idle_screens: ["weather", "clockidle"] }]));
    fireEvent.click((await menuOf("Weather")).getByRole("menuitem", { name: "Move down" }));
    await waitFor(() => expect(sent()[1]).toEqual({ idle_screens: ["clockidle", "weather"] }));
  });

  it("a single screen has a disabled move track, no rotation and no order line, but can be removed", async () => {
    // The order track is always present; with one screen both move buttons are disabled (ends of the list).
    supported(["weather"]);
    render(<IdlePage />);
    await row("Weather");
    expect(screen.getByRole("button", { name: "Move Weather up to position 0" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Move Weather down to position 2" })).toBeDisabled();
    expect(screen.queryByLabelText("Change screen every")).toBeNull();
    expect(screen.queryByText(/^Order:/)).toBeNull();
    expect((await menuOf("Weather")).getByRole("menuitem", { name: "Remove from standby screens" })).not.toBeDisabled();
  });

  it("ignores a saved id that is no longer installed", async () => {
    backend({ screens: screens(), settings: { idle_screens: ["gone"] }, status: { idle_supported: true } });
    render(<IdlePage />);
    await screen.findByRole("button", { name: "Add Weather" });
    expect(screen.queryByRole("group", { name: "gone" })).toBeNull();
    expect(screen.getByText("Send standby screens to the panel")).toBeDisabled();
  });

  it("shows a rejected change", async () => {
    supported();
    __mock.on("set_settings", () => ({ ok: false, error: "idle_screens: 'weather' is not an installed screen with a standby layout" }));
    render(<IdlePage />);
    await screen.findByText("Panel connected");
    add("Weather");
    expect(await screen.findByRole("alert")).toHaveTextContent("not an installed screen");
  });

  it("keeps the Add button locked while a change is being saved", async () => {
    // Row controls are no longer busy-locked (focus must survive); only Available Add and the rotation dropdown lock while busy.
    supported(["weather"]);
    let release: (v: unknown) => void = () => undefined;
    __mock.on("set_settings", () => new Promise((r) => { release = r; }));
    render(<IdlePage />);
    await screen.findByText("Panel connected");
    add("Clock idle");
    await waitFor(() => expect(screen.getByRole("button", { name: "Add Clock idle" })).toBeDisabled());
    release({ ok: true });
    await waitFor(() => expect(screen.getByRole("button", { name: "Add Clock idle" })).not.toBeDisabled());
  });
});

describe("IdlePage render mode and rotation", () => {
  it("offers 4-gray in the standby sheet only where the layout compiles in both modes, and saves the whole map", async () => {
    backend({ screens: screens(["weather", "clockidle"]), settings: { idle_screens: ["weather", "clockidle"], idle_modes: { clockidle: "mono" } }, status: { idle_supported: true } });
    render(<IdlePage />);
    await row("Weather");
    expect((await row("Clock idle")).getByRole("button", { name: /^Settings/ })).toBeDisabled(); // one mode: nothing to configure, so the button is there but disabled
    expect((await row("Weather")).getByText("Black and white")).toBeInTheDocument(); // the mode is a row badge now
    const w = await sheetOf("Weather");
    expect(w.getByRole("switch", { name: "4-gray rendering" })).not.toBeChecked();
    fireEvent.click(w.getByRole("switch", { name: "4-gray rendering" }));
    await waitFor(() => expect(sent()).toEqual([{ idle_modes: { clockidle: "mono", weather: "gray4" } }]));
  });

  it("shows the saved mode in the sheet, and switching it off goes back to black and white", async () => {
    backend({ screens: screens(["weather"]), settings: { idle_screens: ["weather"], idle_modes: { weather: "gray4" } }, status: { idle_supported: true } });
    render(<IdlePage />);
    expect((await row("Weather")).getByText("4 grays")).toBeInTheDocument(); // row badge
    const w = await sheetOf("Weather");
    expect(w.getByRole("switch", { name: "4-gray rendering" })).toBeChecked();
    fireEvent.click(w.getByRole("switch", { name: "4-gray rendering" }));
    await waitFor(() => expect(sent()).toEqual([{ idle_modes: { weather: "mono" } }]));
  });

  it("falls back to the mode the backend would upload", async () => {
    backend({ screens: [scr("w", { has_idle: true, name: "W", idle_modes: ["mono", "gray4"], idle_mode: "gray4" })], settings: { idle_screens: ["w"] }, status: { idle_supported: true } });
    render(<IdlePage />);
    expect((await row("W")).getByText("4 grays")).toBeInTheDocument();
  });

  it("changes the rotation time, including a saved value that is not in the list", async () => {
    backend({ screens: screens(["weather", "clockidle"]), settings: { idle_screens: ["weather", "clockidle"], idle_rotate_s: 45 }, status: { idle_supported: true } });
    render(<IdlePage />);
    const r = await screen.findByLabelText("Change screen every");
    expect(r).toHaveValue("45");
    expect(screen.getByText("Order: Weather → Clock idle. It changes every 45 seconds.")).toBeInTheDocument();
    fireEvent.change(r, { target: { value: "0" } });
    await waitFor(() => expect(sent()).toEqual([{ idle_rotate_s: 0 }]));
  });

  it("says it stays on the first screen when rotation is off", async () => {
    backend({ screens: screens(["weather", "clockidle"]), settings: { idle_screens: ["weather", "clockidle"], idle_rotate_s: 0 }, status: { idle_supported: true } });
    render(<IdlePage />);
    expect(await screen.findByText("Order: Weather → Clock idle. It stays on the first one.")).toBeInTheDocument();
  });
});

describe("IdlePage upload status", () => {
  it.each([
    [{ state: "uploading" }, "Sending standby screens", "Sending standby screens to the panel…"],
    [{ state: "ok", on_panel: ["weather", "clockidle"], bytes: 2048, free: 49152 }, STANDBY_ON_PANEL, "On the panel: Weather, Clock idle (2.0 KB of 48.0 KB)"],
    [{ state: "pending", on_panel: ["weather"] }, "Waiting to send", "On the panel now: Weather. Changes are sent when it connects."],
  ] as [Partial<IdleStatus>, string, string][])("%o", async (idle, title, text) => {
    supported(["weather", "clockidle"], idle);
    render(<IdlePage />);
    expect(await screen.findByText(title)).toBeInTheDocument();
    expect(screen.getByText(text)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows how much of the panel's storage is used", async () => {
    supported(["weather"], { state: "ok", on_panel: ["weather"], bytes: 12288, free: 49152 });
    render(<IdlePage />);
    expect(await screen.findByRole("progressbar", { name: "Panel storage used" })).toHaveAttribute("aria-valuenow", "25");
  });

  it("never shows more than 100% of the storage", async () => {
    supported(["weather"], { state: "ok", on_panel: ["weather"], bytes: 99999, free: 49152 });
    render(<IdlePage />);
    expect(await screen.findByRole("progressbar", { name: "Panel storage used" })).toHaveAttribute("aria-valuenow", "100");
  });

  it("shows why an upload failed as an error", async () => {
    supported(["weather"], { state: "error", error: "the panel refused the standby layouts: STORAGE" });
    render(<IdlePage />);
    const a = await screen.findByRole("alert");
    expect(a).toHaveTextContent(STANDBY_NOT_SENT); // "Standby screens not sent"
    expect(a).toHaveTextContent("the panel refused the standby layouts: STORAGE");
  });

  it("shows a compile error for a screen", async () => {
    supported(["weather"], { state: "error", error: "weather: idle.widgets[0].text: unknown source 'nope'" });
    render(<IdlePage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("weather: idle.widgets[0].text");
  });

  it("an upload error without a message still says something", async () => {
    supported(["weather"], { state: "error", error: null });
    render(<IdlePage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("unknown error");
  });

  it("follows status events", async () => {
    supported(["weather"], { state: "uploading" });
    render(<IdlePage />);
    await screen.findByText("Sending standby screens to the panel…");
    __mock.on("get_status", () => status({ idle_supported: true, idle: idleSt({ state: "ok", ids: ["weather"], on_panel: ["weather"], bytes: 1024 }) }));
    act(() => __mock.emit("status"));
    expect(await screen.findByText(/On the panel: Weather/)).toBeInTheDocument();
  });

  it("the firmware gets updated while the page is open", async () => {
    backend({ screens: screens(), status: { idle_supported: false, idle: idleSt({ state: "unsupported" }) } });
    render(<IdlePage />);
    await screen.findByText(STANDBY_UNSUPPORTED);
    __mock.on("get_status", () => status({ idle_supported: true }));
    act(() => __mock.emit("status"));
    await waitFor(() => expect(screen.queryByText(STANDBY_UNSUPPORTED)).toBeNull());
  });
});

describe("IdlePage push", () => {
  it("pushes and says it started", async () => {
    supported(["weather"]);
    __mock.on("push_idle_layouts", () => ({ ok: true, supported: true, idle: idleSt({ state: "uploading" }) }));
    render(<IdlePage />);
    const b = await screen.findByText("Send standby screens to the panel");
    expect(b).not.toBeDisabled();
    fireEvent.click(b);
    expect(await screen.findByText("Sending standby screens to the panel…")).toBeInTheDocument();
    expect(__mock.callsTo("push_idle_layouts")).toHaveLength(1);
  });

  it("reports a refused push", async () => {
    supported(["weather"]);
    __mock.on("push_idle_layouts", () => ({ ok: false, error: "weather: compiled layout 9000 bytes > 8192", supported: true }));
    render(<IdlePage />);
    fireEvent.click(await screen.findByText("Send standby screens to the panel"));
    expect(await screen.findByRole("alert")).toHaveTextContent("compiled layout 9000 bytes > 8192");
    expect(screen.queryByText("Sending standby screens to the panel…")).toBeNull();
  });
});

describe("IdlePage Wi-Fi status row", () => {
  it.each([
    [{ wifi_ssid: "", wifi_pass: "unset" as const }, "No Wi-Fi network set"],
    [{ wifi_ssid: "HomeNet", wifi_pass: "set" as const }, "Wi-Fi: HomeNet"],
    [{ wifi_ssid: "Cafe", wifi_pass: "unset" as const }, "Wi-Fi: Cafe (no password)"],
  ])("%o shows %s in the overview", async (cfg, title) => {
    supported([], {}, { settings: cfg });
    render(<IdlePage />);
    expect((await overview()).getByRole("status", { name: title })).toBeInTheDocument();
  });

  it("links to the Settings page (Connections group) and holds no Wi-Fi form", async () => {
    supported();
    render(<IdlePage />);
    fireEvent.click(await screen.findByRole("button", { name: "Open Wi-Fi settings" }));
    expect(Navigation.Navigate).toHaveBeenCalledWith("/inkterface/settings");
    expect(screen.queryByRole("region", { name: "Panel Wi-Fi" })).toBeNull();
    expect(screen.queryByLabelText("Network name (SSID)")).toBeNull();
    expect(screen.queryByLabelText(/password/i)).toBeNull();
  });
});

describe("IdlePage previews", () => {
  const idleCalls = () => __mock.callsTo("preview_idle_screen");
  it("every row shows a picture from the idle renderer, in the mode it will be uploaded in", async () => {
    backend({ screens: screens(["weather"]), settings: { idle_screens: ["weather"], idle_modes: { weather: "gray4" } }, status: { idle_supported: true } });
    render(<IdlePage />);
    const imgs = await screen.findAllByRole("img");
    expect(imgs.length).toBeGreaterThanOrEqual(2); // weather (chosen, + Overview art) and clock idle (available)
    for (const i of imgs) expect(i).toHaveAttribute("src", PNG);
    expect(idleCalls()).toEqual(expect.arrayContaining([["weather", "gray4"], ["clockidle", "mono"]]));
    expect(__mock.callsTo("preview_screen")).toEqual([]); // never the active look
  });

  it("re-renders with the new mode once the setting arrives", async () => {
    supported(["weather"]);
    render(<IdlePage />);
    await screen.findAllByRole("img");
    const base = __mock.handlers.get("get_settings")!() as object;
    __mock.on("get_settings", () => ({ ...base, idle_modes: { weather: "gray4" } }));
    act(() => __mock.emit("settings_changed"));
    await waitFor(() => expect(idleCalls()).toContainEqual(["weather", "gray4"]));
  });

  it("says when a picture could not be drawn and shows a placeholder while drawing", async () => {
    supported(["weather"]);
    __mock.on("preview_idle_screen", (id: string) => (id === "weather" ? { ok: false, error: "idle preview renderer is not installed" } : new Promise(() => undefined)));
    render(<IdlePage />);
    expect(await (await row("Weather")).findByText("No preview")).toBeInTheDocument();
    expect(await screen.findByRole("progressbar", { name: "Rendering Clock idle" })).toBeInTheDocument();
  });

  it("does not ask for a picture of code that waits for approval", async () => {
    backend({
      screens: [scr("code", { builtin: false, has_code: true, has_idle: true, has_active: false, approved: false, needs_approval: true, enabled: false, idle_modes: ["mono"] })],
      status: { idle_supported: true },
    });
    render(<IdlePage />);
    const c = await row("Code");
    expect(c.getByText("Needs your approval")).toBeInTheDocument();
    expect(c.getByRole("button", { name: "Add Code" })).toBeDisabled();
    expect(idleCalls()).toEqual([]);
  });

  it("a standby-only screen is not dimmed as if it were off", async () => {
    supported();
    render(<IdlePage />);
    expect(await screen.findByRole("group", { name: "Clock idle" })).not.toHaveAttribute("data-off");
  });
});

describe("IdlePage errors", () => {
  it("backend down: loading, then an error with a retry that recovers", async () => {
    __mock.on("get_status", () => { throw new Error("down"); });
    __mock.on("list_screens", () => { throw new Error("down"); });
    __mock.on("get_settings", () => { throw new Error("down"); });
    render(<IdlePage />);
    expect(screen.getByText("Loading…")).toBeInTheDocument();
    expect(await screen.findByRole("alert")).toHaveTextContent("Cannot load screens: down");
    supported(["weather"]);
    fireEvent.click(screen.getByText("Try again"));
    expect(await row("Weather")).toBeTruthy();
  });

  it("settings unreadable", async () => {
    backend({ screens: screens() });
    __mock.on("get_settings", () => { throw new Error("no settings"); });
    render(<IdlePage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Cannot load screens: no settings");
  });

  it("status unreadable but the rest fine: controls stay disabled and the error is shown", async () => {
    backend({ screens: screens() });
    __mock.on("get_status", () => { throw new Error("no status"); });
    render(<IdlePage />);
    expect(await screen.findByText("Cannot read panel status: no status")).toBeInTheDocument();
    expect(screen.getByText("Panel status unknown")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add Weather" })).toBeDisabled();
    expect(screen.getByText("Send standby screens to the panel")).toBeDisabled();
  });

  it("status unreadable: chosen screens cannot be changed either", async () => {
    backend({ screens: screens(["weather", "clockidle"]), settings: { idle_screens: ["weather", "clockidle"] } });
    __mock.on("get_status", () => { throw new Error("no status"); });
    render(<IdlePage />);
    await row("Weather");
    expect(screen.getByRole("button", { name: "More for Weather" })).toBeDisabled(); // the menu is locked
    expect(screen.getByRole("button", { name: "Move Weather down to position 2" })).toBeDisabled();
    expect(screen.getByLabelText("Change screen every")).toBeDisabled();
    const w = await sheetOf("Weather"); // the sheet still opens but its switch is locked while the status is unknown
    expect(w.getByRole("switch", { name: "4-gray rendering" })).toBeDisabled();
  });
});

describe("uploadLine", () => {
  const names = { a: "Alpha" };
  it("is silent when nothing is happening", () => {
    expect(uploadLine(idleSt({ state: "none" }), names, 0)).toBeNull();
    expect(uploadLine(idleSt({ state: "unsupported" }), names, 1)).toBeNull(); // the page shows its own explanation
    expect(uploadLine(idleSt({ state: "pending" }), names, 0)).toBeNull(); // nothing chosen: nothing is waiting
  });
  it("falls back to the id of an unknown screen and the default capacity", () => {
    expect(uploadLine(idleSt({ state: "ok", on_panel: ["zzz"], bytes: 1024, free: null }), names, 1)?.text).toBe("On the panel: zzz (1.0 KB of 48.0 KB)");
    expect(uploadLine(idleSt({ state: "error", error: null }), names, 1)).toEqual({ text: "Not sent to the panel: unknown error", error: true });
  });
});

describe("wifiStatus", () => {
  it("no network is a warning that says what it costs", () => {
    expect(wifiStatus({ wifi_ssid: "", wifi_pass: "unset" })).toMatchObject({ tone: "warn", title: "No Wi-Fi network set" });
  });
  it("a password with no network name is still not set up", () => {
    expect(wifiStatus({ wifi_ssid: "", wifi_pass: "set" }).tone).toBe("warn");
  });
  it("network and password is ready; network alone is an open network", () => {
    expect(wifiStatus({ wifi_ssid: "Home", wifi_pass: "set" })).toMatchObject({ tone: "ok", title: "Wi-Fi: Home" });
    expect(wifiStatus({ wifi_ssid: "Home", wifi_pass: "unset" })).toMatchObject({ tone: "info", title: "Wi-Fi: Home (no password)" });
  });
});
