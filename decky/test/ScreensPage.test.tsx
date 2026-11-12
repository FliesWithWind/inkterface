import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SettingDef } from "../src/api";
import { ScreensPage, duration } from "../src/pages/ScreensPage";
import { PNG, backend, screen as scr, status } from "./fixtures";
import { __mock } from "./mocks/decky-api";

const calls = (n: string) => __mock.callsTo(n);
const card = (name: string) => screen.findByRole("group", { name });
const region = async (name: string) => within(await screen.findByRole("region", { name }));
/** The in-rotation rows (role=group with data-row-id) inside a scope, in order. Filters out the nested OrderTrack groups. */
const orderedNames = (scope: ReturnType<typeof within>) =>
  scope.getAllByRole("group").filter((g: HTMLElement) => g.hasAttribute("data-row-id")).map((g: HTMLElement) => g.getAttribute("aria-label"));
/** The available rows inside a scope (no order track, so every group is a row). */
const availableNames = (scope: ReturnType<typeof within>) => scope.getAllByRole("group").map((g: HTMLElement) => g.getAttribute("aria-label"));
/** Opens a row's "..." menu and returns it. The menu is rendered into document.body, query via `screen`. */
const openMenu = async (name: string) => {
  fireEvent.click(within(await card(name)).getByRole("button", { name: `More for ${name}` }));
  return within(await screen.findByRole("menu"));
};
const chooseMenu = async (row: string, item: string | RegExp) => fireEvent.click((await openMenu(row)).getByRole("menuitem", { name: item }));
/** Opens a row's Settings sheet (a modal dialog in document.body) and returns it. */
const sheetOf = async (name: string) => {
  fireEvent.click(within(await card(name)).getByRole("button", { name: /^Settings/ }));
  return within(await screen.findByRole("dialog", { name }));
};

const SCHEMA: SettingDef[] = [
  { key: "show", type: "bool", label: "Show seconds", default: false },
  { key: "size", type: "number", label: "Size", default: 3, min: 1, max: 9 },
  { key: "offset", type: "number", label: "Offset", default: 0.5 },
  { key: "mode", type: "enum", label: "Mode", default: "a", values: ["a", "b"] },
  { key: "title", type: "string", default: "hi" },
];

const three = () => [
  scr("telemetry", { current: true, description: "CPU and GPU load" }),
  scr("clock"),
  scr("extra", { builtin: false, author: "Ada" }),
];

describe("duration", () => {
  it.each([[5, "5 s"], [59, "59 s"], [60, "1 min"], [90, "1 min 30 s"], [3600, "1 h"], [3660, "1 h 1 min"], [7200, "2 h"]])("%i", (n, text) => {
    expect(duration(n)).toBe(text);
  });
});

describe("ScreensPage flow: in rotation, then available", () => {
  it("does not repeat the page title: Steam's SidebarNavigation draws it, so the page has no h1 and no 'Live Screens' heading", async () => {
    backend({ screens: three() });
    render(<ScreensPage />);
    await card("Clock");
    expect(screen.queryByRole("heading", { level: 1 })).toBeNull();
    expect(screen.queryByRole("heading", { name: "Live Screens" })).toBeNull();
    expect(screen.getByRole("heading", { name: "In rotation" })).toBeInTheDocument();
  });

  it("lists the rotation in order, numbered, and the rest under Available", async () => {
    backend({ screens: three(), settings: { rotation: ["clock", "gone", "telemetry"] } });
    render(<ScreensPage />);
    await card("Clock");
    expect(orderedNames(await region("In rotation"))).toEqual(["Clock", "Telemetry"]); // a saved id that is not installed is skipped
    expect(within(await card("Clock")).getByRole("group", { name: "Position 1 of 2" })).toHaveTextContent("1");
    expect(within(await card("Telemetry")).getByRole("group", { name: "Position 2 of 2" })).toHaveTextContent("2");
    expect(availableNames(await region("Available"))).toEqual(["Extra"]);
  });

  it("every row has a picture, name, state badges, origin and description", async () => {
    backend({ screens: three(), settings: { rotation: ["telemetry", "clock"] } });
    render(<ScreensPage />);
    const t = within(await card("Telemetry"));
    expect(t.getByText("On panel")).toBeInTheDocument();
    expect(t.getByText("Built-in · v1.0.0")).toBeInTheDocument();
    expect(t.getByText("CPU and GPU load")).toBeInTheDocument();
    expect(await t.findByRole("img", { name: "Preview of Telemetry" })).toHaveAttribute("src", PNG);
    const e = within(await card("Extra"));
    expect(e.getByText("by Ada · v1.0.0")).toBeInTheDocument();
    expect(e.queryByText("On panel")).toBeNull();
    // telemetry is previewed twice: once in its row, once as the picture on the Panel status card (it is current).
    expect(calls("preview_screen").map((a) => a[0]).sort()).toEqual(["clock", "extra", "telemetry", "telemetry"]);
    expect(calls("preview_idle_screen")).toEqual([]);
  });

  it("badges say pinned and off in words", async () => {
    backend({ screens: [scr("telemetry", { pinned: true, current: true }), scr("clock", { enabled: false })], settings: { rotation: ["telemetry", "clock"] } });
    render(<ScreensPage />);
    expect(within(await card("Telemetry")).getByText("Pinned")).toBeInTheDocument();
    expect(within(await card("Clock")).getByText("Off")).toBeInTheDocument();
  });

  it("add puts a screen at the end of the rotation and keeps the interval", async () => {
    backend({ screens: three(), settings: { rotation: ["telemetry", "clock"], rotation_interval_s: 45 } });
    render(<ScreensPage />);
    fireEvent.click(within(await card("Extra")).getByRole("button", { name: "Add Extra" }));
    await waitFor(() => expect(calls("set_rotation")).toEqual([[["telemetry", "clock", "extra"], 45]]));
    expect(calls("set_screen_enabled")).toEqual([]); // already on
  });

  it("adding a screen that is switched off turns it on first, and stops if that fails", async () => {
    backend({ screens: [scr("telemetry"), scr("clock", { enabled: false })], settings: { rotation: ["telemetry"] } });
    render(<ScreensPage />);
    fireEvent.click(within(await card("Clock")).getByRole("button", { name: "Add Clock" }));
    await waitFor(() => expect(calls("set_rotation")).toEqual([[["telemetry", "clock"], 30]]));
    expect(calls("set_screen_enabled")).toEqual([["clock", true]]);
  });

  it("a failed enable shows why and does not touch the rotation", async () => {
    backend({ screens: [scr("telemetry"), scr("clock", { enabled: false })], settings: { rotation: ["telemetry"] } });
    __mock.on("set_screen_enabled", () => ({ ok: false, error: "clock: needs approval" }));
    render(<ScreensPage />);
    fireEvent.click(within(await card("Clock")).getByRole("button", { name: "Add Clock" }));
    expect(await screen.findByText("clock: needs approval")).toBeInTheDocument();
    expect(calls("set_rotation")).toEqual([]);
  });

  it("remove through the menu takes a screen out of the rotation and keeps the interval", async () => {
    backend({ screens: three(), settings: { rotation: ["telemetry", "clock"], rotation_interval_s: 45 } });
    render(<ScreensPage />);
    await chooseMenu("Clock", "Remove from rotation");
    await waitFor(() => expect(calls("set_rotation")).toEqual([[["telemetry"], 45]]));
  });

  it("the last screen cannot be removed (the backend refuses an empty rotation): a message, no call, no Undo", async () => {
    backend({ screens: three(), settings: { rotation: ["clock"] } });
    render(<ScreensPage />);
    await chooseMenu("Clock", "Remove from rotation");
    expect(await screen.findByRole("status")).toHaveTextContent("Clock cannot be removed: the rotation needs at least one screen.");
    expect(calls("set_rotation")).toEqual([]);
    expect(screen.queryByRole("button", { name: "Undo" })).toBeNull();
  });

  it("moves a screen up and down with the order-track buttons, optimistically", async () => {
    backend({ screens: three(), settings: { rotation: ["telemetry", "clock", "extra"], rotation_interval_s: 20 } });
    render(<ScreensPage />);
    await card("Clock");
    fireEvent.click(screen.getByRole("button", { name: "Move Clock up to position 1" }));
    await waitFor(() => expect(calls("set_rotation")[0]).toEqual([["clock", "telemetry", "extra"], 20]));
    // the row order changed in place (local state), so Clock is now first: moving it back down
    fireEvent.click(await screen.findByRole("button", { name: "Move Clock down to position 2" }));
    await waitFor(() => expect(calls("set_rotation")[1]).toEqual([["telemetry", "clock", "extra"], 20]));
  });

  it("the first cannot move up and the last cannot move down; a single screen's move buttons are disabled", async () => {
    backend({ screens: three(), settings: { rotation: ["telemetry", "clock"] } });
    const { unmount } = render(<ScreensPage />);
    await card("Clock");
    expect(screen.getByRole("button", { name: "Move Telemetry up to position 0" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Move Telemetry down to position 2" })).not.toBeDisabled();
    expect(screen.getByRole("button", { name: "Move Clock down to position 3" })).toBeDisabled();
    unmount();
    backend({ screens: three(), settings: { rotation: ["clock"] } });
    render(<ScreensPage />);
    await card("Clock");
    expect(screen.getByRole("button", { name: "Move Clock up to position 0" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Move Clock down to position 2" })).toBeDisabled();
  });

  it("a rejected move shows the reason and rolls the order back", async () => {
    backend({ screens: three(), settings: { rotation: ["telemetry", "clock"] } });
    __mock.on("set_rotation", () => ({ ok: false, error: "screen 'clock' is not enabled" }));
    render(<ScreensPage />);
    await card("Clock");
    fireEvent.click(screen.getByRole("button", { name: "Move Clock up to position 1" }));
    expect(await screen.findByText("screen 'clock' is not enabled")).toBeInTheDocument();
    await waitFor(() => expect(orderedNames(within(screen.getByRole("region", { name: "In rotation" })))).toEqual(["Telemetry", "Clock"]));
  });

  it("does not render code that is not approved, cannot add it, and says why", async () => {
    backend({
      screens: [scr("telemetry"), scr("code", { builtin: false, has_code: true, enabled: false, needs_approval: true, approved: false }),
        scr("off", { builtin: false, has_code: true, enabled: false, approved: true })],
      settings: { rotation: ["telemetry"] },
    });
    render(<ScreensPage />);
    const c = within(await card("Code"));
    expect(await c.findByText("Needs your approval")).toBeInTheDocument();
    expect(c.getByText(/Waiting for your approval/)).toBeInTheDocument();
    expect(c.getByText("Needs approval")).toBeInTheDocument();
    expect(c.getByText(/runs code/)).toBeInTheDocument();
    expect(c.getByRole("button", { name: "Add Code" })).toBeDisabled();
    expect(within(await card("Off")).getByText("Switch on to preview")).toBeInTheDocument();
    // unapproved or switched-off code is never run for a picture; telemetry is drawn twice (its row + the Panel card).
    expect(calls("preview_screen").map((a) => a[0])).toEqual(["telemetry", "telemetry"]);
  });

  it("shows a screen's own load error", async () => {
    backend({ screens: [scr("telemetry", { error: "settings.zzz: unknown setting" })] });
    render(<ScreensPage />);
    const c = within(await card("Telemetry"));
    expect(c.getByRole("alert")).toHaveTextContent("settings.zzz: unknown setting");
    expect(c.getByText("Error")).toBeInTheDocument();
  });

  it("shows a placeholder while the picture renders, and says when it failed", async () => {
    backend({ screens: [scr("clock"), scr("broken")], settings: { rotation: [] } });
    __mock.on("preview_screen", (id: string) => (id === "broken" ? { ok: false, error: "boom" } : new Promise(() => undefined)));
    render(<ScreensPage />);
    expect(await screen.findByRole("progressbar", { name: "Rendering Clock" })).toBeInTheDocument();
    expect(await within(await card("Broken")).findByText("No preview")).toBeInTheDocument();
  });

  it("two screens keep their own picture and text", async () => {
    backend({ screens: [scr("alpha", { description: "Alpha text" }), scr("beta", { description: "Beta text" })], settings: { rotation: ["alpha"] } });
    __mock.on("preview_screen", (id: string) => ({ ok: true, data_url: id === "alpha" ? "data:image/png;base64,AAAA" : "data:image/png;base64,BBBB" }));
    render(<ScreensPage />);
    const a = within(await card("Alpha"));
    const b = within(await card("Beta"));
    expect(await a.findByRole("img")).toHaveAttribute("src", "data:image/png;base64,AAAA");
    expect(await b.findByRole("img")).toHaveAttribute("src", "data:image/png;base64,BBBB");
    expect(a.queryByText("Beta text")).toBeNull();
  });

  it("editing one screen's settings re-renders only that screen", async () => {
    backend({ screens: [scr("alpha"), scr("beta")], settings: { rotation: ["alpha", "beta"] } });
    render(<ScreensPage />);
    await within(await card("Alpha")).findByRole("img");
    await within(await card("Beta")).findByRole("img");
    expect(calls("preview_screen")).toHaveLength(2); // no Panel picture: the current screen (telemetry) is not installed
    __mock.on("list_screens", () => [scr("alpha", { settings: { x: 1 } }), scr("beta")]);
    act(() => __mock.emit("screens_changed"));
    await waitFor(() => expect(calls("preview_screen").map((c) => c[0])).toEqual(["alpha", "beta", "alpha"]));
  });
});

describe("ScreensPage pin through the menu", () => {
  it("pin and unpin from the row menu", async () => {
    backend({ screens: [scr("telemetry", { pinned: true }), scr("clock")], settings: { rotation: ["telemetry", "clock"], pinned: "telemetry" } });
    render(<ScreensPage />);
    await chooseMenu("Clock", "Pin to panel");
    await waitFor(() => expect(calls("pin_screen")).toEqual([["clock"]]));
    expect(within(await card("Telemetry")).getByText("Pinned")).toBeInTheDocument();
    await chooseMenu("Telemetry", "Unpin from panel");
    await waitFor(() => expect(calls("unpin_screen")).toHaveLength(1));
  });

  it("a switched-off screen cannot be pinned until it is on", async () => {
    backend({ screens: [scr("telemetry"), scr("clock", { enabled: false })], settings: { rotation: ["telemetry", "clock"] } });
    render(<ScreensPage />);
    expect((await openMenu("Clock")).getByRole("menuitem", { name: "Pin to panel" })).toBeDisabled();
    expect((await openMenu("Telemetry")).getByRole("menuitem", { name: "Pin to panel" })).not.toBeDisabled();
  });

  it("shows a refusal from the backend, and a failed enable from the sheet", async () => {
    backend({ screens: three() });
    __mock.on("pin_screen", () => ({ ok: false, error: "'clock' is not an enabled active screen" }));
    __mock.on("set_screen_enabled", () => { throw new Error("socket closed"); });
    render(<ScreensPage />);
    await chooseMenu("Clock", "Pin to panel");
    expect(await screen.findByText("'clock' is not an enabled active screen")).toBeInTheDocument();
    const s = await sheetOf("Clock");
    fireEvent.click(s.getByRole("switch", { name: "Enabled" }));
    expect(await screen.findByText("socket closed")).toBeInTheDocument();
  });
});

describe("ScreensPage settings sheet", () => {
  it("opens a modal with Enabled, no Done, and closes on B", async () => {
    backend({ screens: three() });
    render(<ScreensPage />);
    fireEvent.click(within(await card("Clock")).getByRole("button", { name: /^Settings/ }));
    const d = within(await screen.findByRole("dialog", { name: "Clock" }));
    expect(d.getByRole("switch", { name: "Enabled" })).toBeInTheDocument();
    expect(d.queryByRole("button", { name: "Done" })).toBeNull();
    fireEvent.keyDown(screen.getByRole("dialog", { name: "Clock" }), { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Clock" })).toBeNull());
  });

  it("available rows have no Settings button (only Add)", async () => {
    backend({ screens: three(), settings: { rotation: ["telemetry", "clock"] } });
    render(<ScreensPage />);
    const e = within(await card("Extra"));
    expect(e.getByRole("button", { name: "Add Extra" })).toBeInTheDocument();
    expect(e.queryByRole("button", { name: /^Settings/ })).toBeNull();
  });

  it("enable and disable live in the sheet", async () => {
    backend({ screens: three() });
    render(<ScreensPage />);
    const s = await sheetOf("Clock");
    expect(s.getByRole("switch", { name: "Enabled" })).toBeChecked();
    fireEvent.click(s.getByRole("switch", { name: "Enabled" }));
    await waitFor(() => expect(calls("set_screen_enabled")).toEqual([["clock", false]]));
  });

  it("a screen that waits for approval cannot be enabled", async () => {
    backend({ screens: [scr("code", { builtin: false, has_code: true, enabled: false, needs_approval: true, approved: false })], settings: { rotation: ["code"] } });
    render(<ScreensPage />);
    expect((await sheetOf("Code")).getByRole("switch", { name: "Enabled" })).toBeDisabled();
  });
});

describe("ScreensPage list states", () => {
  it("says so when nothing is installed", async () => {
    backend({ screens: [] });
    render(<ScreensPage />);
    expect(await screen.findByText(/No screens are installed/)).toBeInTheDocument();
    expect(screen.getByText(/Nothing in the rotation/)).toBeInTheDocument();
  });

  it("says when every screen is already in the rotation", async () => {
    backend({ screens: [scr("telemetry")], settings: { rotation: ["telemetry"] } });
    render(<ScreensPage />);
    expect(await screen.findByText("Every screen is already in the rotation.")).toBeInTheDocument();
  });

  it("standby-only screens are not listed; one line points to the Standby Screens page", async () => {
    backend({ screens: [scr("idle", { has_active: false, has_idle: true, enabled: false }), scr("telemetry"), scr("clock")] });
    render(<ScreensPage />);
    await card("Telemetry");
    expect(screen.queryByRole("group", { name: "Idle" })).toBeNull();
    expect(screen.getByText(/^1 screen is drawn by the panel itself.*Choose it on the Standby Screens page/)).toBeInTheDocument();
  });

  it("no standby-only line when every screen can be shown (the static pointer stays)", async () => {
    backend({ screens: three() });
    render(<ScreensPage />);
    await card("Clock");
    expect(screen.queryByText(/^\d+ screens? (is|are) drawn by the panel itself/)).toBeNull();
    expect(screen.getByText(/Standby screens are drawn by the panel itself/)).toBeInTheDocument(); // the always-present pointer
  });

  it("shows loading, then a clear error with a way to retry when the backend is down", async () => {
    __mock.on("list_screens", () => { throw new Error("Inkterface is not running"); });
    __mock.on("get_settings", () => { throw new Error("Inkterface is not running"); });
    render(<ScreensPage />);
    expect(screen.getByText("Loading…")).toBeInTheDocument();
    expect(await screen.findByRole("alert")).toHaveTextContent("Cannot load screens: Inkterface is not running");
    backend({ screens: three() });
    fireEvent.click(screen.getByText("Try again"));
    expect(await card("Telemetry")).toBeInTheDocument();
  });

  it("keeps showing the list when a refresh fails", async () => {
    backend({ screens: three() });
    render(<ScreensPage />);
    await card("Clock");
    __mock.on("get_settings", () => { throw new Error("hiccup"); });
    act(() => __mock.emit("settings_changed"));
    expect(await screen.findByText("Refresh failed: hiccup")).toBeInTheDocument();
    expect(await card("Clock")).toBeInTheDocument();
  });

  it("reloads on screens_changed", async () => {
    backend({ screens: [scr("telemetry")] });
    render(<ScreensPage />);
    await card("Telemetry");
    __mock.on("list_screens", () => [scr("telemetry"), scr("fresh", { builtin: false })]);
    act(() => __mock.emit("screens_changed"));
    expect(await card("Fresh")).toBeInTheDocument();
  });
});

describe("ScreensPage panel status card", () => {
  it("shows what the panel is showing, with firmware and standby facts", async () => {
    backend({ screens: three(), status: { current_screen: "clock", pinned: "clock" } });
    render(<ScreensPage />);
    const p = await region("Panel");
    expect(p.getByText("Panel connected")).toBeInTheDocument();
    expect(p.getByText("Showing Clock (pinned)")).toBeInTheDocument();
    // facts are <dt>label</dt><dd>value</dd> pairs; scope the value to its own pair.
    const factValue = (label: string) => within(p.getByText(label).closest("div") as HTMLElement);
    expect(factValue("Firmware").getByText("1.2.3")).toBeInTheDocument();
    expect(factValue("Standby after").getByText("1 min")).toBeInTheDocument(); // idle_timeout_s 60
  });

  it("follows the panel: the On panel badge moves when the status changes", async () => {
    backend({ screens: three() });
    render(<ScreensPage />);
    expect(within(await card("Telemetry")).getByText("On panel")).toBeInTheDocument();
    __mock.on("get_status", () => status({ current_screen: "clock" }));
    act(() => __mock.emit("status"));
    await waitFor(() => expect(within(screen.getByRole("group", { name: "Clock" })).getByText("On panel")).toBeInTheDocument());
    expect(within(screen.getByRole("group", { name: "Telemetry" })).queryByText("On panel")).toBeNull();
  });

  it("no screen is on the panel while it is unplugged", async () => {
    backend({ screens: three(), status: { connected: false, ui_state: "waiting" } });
    render(<ScreensPage />);
    expect(await (await region("Panel")).findByText(/Waiting for panel/)).toBeInTheDocument();
    expect(within(await card("Telemetry")).queryByText("On panel")).toBeNull();
  });

  it("an unreadable status leaves the list usable", async () => {
    backend({ screens: three() });
    __mock.on("get_status", () => { throw new Error("no status"); });
    render(<ScreensPage />);
    expect(await screen.findByText("Panel status unknown")).toBeInTheDocument();
    expect(await card("Telemetry")).toBeInTheDocument();
  });
});

describe("ScreensPage rotation summary", () => {
  it("summarises the cycle", async () => {
    backend({ screens: three(), settings: { rotation: ["telemetry", "clock", "extra"], rotation_interval_s: 30 } });
    render(<ScreensPage />);
    expect(await screen.findByText(/^3 screens, 30 s each\. A full cycle takes 1 min 30 s\./)).toBeInTheDocument();
  });

  it("says screen in the singular", async () => {
    backend({ screens: three(), settings: { rotation: ["clock"], rotation_interval_s: 60 } });
    render(<ScreensPage />);
    expect(await screen.findByText(/^1 screen, 1 min each\. A full cycle takes 1 min\./)).toBeInTheDocument();
  });

  it("tells you when a pin holds the panel", async () => {
    backend({ screens: three(), settings: { pinned: "clock" } });
    render(<ScreensPage />);
    expect(await screen.findByText(/Clock is pinned, so the panel stays on it/)).toBeInTheDocument();
  });

  it("changes the interval for the screens in the rotation", async () => {
    backend({ screens: three(), settings: { rotation: ["telemetry", "clock"], rotation_interval_s: 30 } });
    render(<ScreensPage />);
    fireEvent.change(await screen.findByLabelText("Seconds per screen"), { target: { value: "60" } });
    await waitFor(() => expect(calls("set_rotation")).toEqual([[["telemetry", "clock"], 60]]));
  });

  it("explains an empty rotation and disables the slider", async () => {
    backend({ screens: three(), settings: { rotation: [] } });
    render(<ScreensPage />);
    expect(await screen.findByText("The panel is not cycling through any screen.")).toBeInTheDocument();
    expect(screen.getByLabelText("Seconds per screen")).toBeDisabled();
  });
});

describe("ScreensPage generated settings form", () => {
  const withSchema = () => backend({
    screens: [scr("telemetry"), scr("cfg", { builtin: false, settings_schema: SCHEMA, settings: { show: true, size: 5, offset: 0.5, mode: "b", title: "yo" } })],
    settings: { rotation: ["telemetry", "cfg"] },
  });

  it("renders a control per setting type with the current values, in the sheet", async () => {
    withSchema();
    render(<ScreensPage />);
    const f = await sheetOf("Cfg");
    expect(f.getByRole("switch", { name: "Show seconds" })).toBeChecked();
    expect(f.getByLabelText("Size")).toHaveValue("5");
    expect(f.getByLabelText("Offset")).toHaveValue("0.5");
    expect(f.getByLabelText("Mode")).toHaveValue("b");
    expect(f.getByLabelText("title")).toHaveValue("yo");
  });

  it("falls back to defaults for settings with no stored value, and has nothing to save or reset", async () => {
    backend({ screens: [scr("cfg", { settings_schema: SCHEMA, settings: {} })], settings: { rotation: ["cfg"] } });
    render(<ScreensPage />);
    const f = await sheetOf("Cfg");
    expect(f.getByRole("switch", { name: "Show seconds" })).not.toBeChecked();
    expect(f.getByLabelText("Mode")).toHaveValue("a");
    expect(f.getByText("Save settings")).toBeDisabled();
    expect(f.getByText("Reset to defaults")).toBeDisabled();
  });

  it("a screen with no settings still has Enabled, but no form", async () => {
    backend({ screens: [scr("plain")], settings: { rotation: ["plain"] } });
    render(<ScreensPage />);
    const f = await sheetOf("Plain");
    expect(f.getByRole("switch", { name: "Enabled" })).toBeInTheDocument();
    expect(f.queryByText("Save settings")).toBeNull();
  });

  it("saves the edited values, says there are unsaved changes first and saved after", async () => {
    withSchema();
    render(<ScreensPage />);
    const f = await sheetOf("Cfg");
    expect(f.queryByText("You have unsaved changes.")).toBeNull();
    fireEvent.click(f.getByRole("switch", { name: "Show seconds" }));
    expect(f.getByText("You have unsaved changes.")).toBeInTheDocument();
    fireEvent.change(f.getByLabelText("Size"), { target: { value: "7" } });
    fireEvent.change(f.getByLabelText("Offset"), { target: { value: "1.25" } });
    fireEvent.change(f.getByLabelText("Mode"), { target: { value: "a" } });
    fireEvent.change(f.getByLabelText("title"), { target: { value: "new" } });
    fireEvent.click(f.getByText("Save settings"));
    await waitFor(() => expect(calls("set_screen_settings")).toEqual([["cfg", { show: false, size: 7, offset: 1.25, mode: "a", title: "new" }]]));
    __mock.on("list_screens", () => [scr("telemetry"), scr("cfg", { builtin: false, settings_schema: SCHEMA, settings: { show: false, size: 7, offset: 1.25, mode: "a", title: "new" } })]);
    act(() => __mock.emit("screens_changed"));
    expect(await f.findByText(/Saved\./)).toBeInTheDocument();
    expect(f.getByLabelText("Size")).toHaveValue("7");
  });

  it("keeps non-numeric text so the backend can reject it, and shows the message", async () => {
    withSchema();
    __mock.on("set_screen_settings", () => ({ ok: false, error: "settings.offset: must be a number, got str" }));
    render(<ScreensPage />);
    const f = await sheetOf("Cfg");
    fireEvent.change(f.getByLabelText("Offset"), { target: { value: "abc" } });
    fireEvent.click(f.getByText("Save settings"));
    await waitFor(() => expect(calls("set_screen_settings")[0][1]).toMatchObject({ offset: "abc" }));
    expect(await f.findByRole("alert")).toHaveTextContent("must be a number");
  });

  it("resets to defaults", async () => {
    withSchema();
    render(<ScreensPage />);
    const f = await sheetOf("Cfg");
    fireEvent.click(f.getByText("Reset to defaults"));
    await waitFor(() => expect(calls("set_screen_settings")).toEqual([["cfg", {}]]));
    await waitFor(() => expect(f.getByLabelText("Size")).toHaveValue("3"));
  });
});
