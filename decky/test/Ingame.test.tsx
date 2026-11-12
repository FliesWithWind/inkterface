// Live Screens: the optional "In a game" list. Same list, same buttons; a toggle and two tabs choose which list is edited.
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ScreensPage } from "../src/pages/ScreensPage";
import { backend, screen as scr, status } from "./fixtures";
import { __mock } from "./mocks/decky-api";

const calls = (n: string) => __mock.callsTo(n);
const card = (name: string) => screen.findByRole("group", { name });
const region = async (name: string) => within(await screen.findByRole("region", { name }));
const rows = (scope: ReturnType<typeof within>) =>
  scope.getAllByRole("group").filter((g: HTMLElement) => g.hasAttribute("data-row-id")).map((g: HTMLElement) => g.getAttribute("aria-label"));
const tab = (name: RegExp) => screen.getByRole("tab", { name });
const SCREENS = () => [scr("telemetry", { current: true }), scr("clock"), scr("now-playing", { name: "Now Playing" })];
const INGAME = { rotation: ["now-playing"], rotation_interval_s: 15, pinned: null };

describe("Different screens while a game is running", () => {
  it("off: a toggle, no tabs, and the page edits the one list exactly as before", async () => {
    backend({ screens: SCREENS() });
    render(<ScreensPage />);
    const t = await screen.findByRole("switch", { name: "Different screens while a game is running" });
    expect(t).not.toBeChecked();
    expect(screen.queryByRole("tablist")).toBeNull();
    expect(rows(await region("In rotation"))).toEqual(["Telemetry", "Clock"]);
    expect(document.querySelector(".ink-prof")).toBeNull(); // no wrapper: the page is exactly as before
    fireEvent.click(within(await card("Clock")).getByRole("button", { name: "Move Clock up to position 1" }));
    await waitFor(() => expect(calls("set_rotation")).toEqual([[["clock", "telemetry"], 30]])); // no profile argument
  });

  it("the toggle turns it on and off", async () => {
    backend({ screens: SCREENS() });
    render(<ScreensPage />);
    fireEvent.click(await screen.findByRole("switch", { name: "Different screens while a game is running" }));
    await waitFor(() => expect(calls("set_settings")).toEqual([[{ ingame_enabled: true }]]));
  });

  it("on: two tabs, the one in use marked In use, starting on it", async () => {
    backend({ screens: SCREENS(), settings: { ingame_enabled: true, ingame: INGAME }, status: { profile: "ingame", now_playing: 570 } });
    render(<ScreensPage />);
    await card("Now Playing");
    expect(screen.getByRole("tablist", { name: "Which screens to edit" })).toBeInTheDocument();
    expect(tab(/^In a game/)).toHaveAttribute("aria-selected", "true");
    expect(tab(/^In a game/)).toHaveTextContent("In use");
    expect(tab(/^Not in a game/)).not.toHaveTextContent("In use");
    expect(rows(await region("In a game · in rotation"))).toEqual(["Now Playing"]);
    expect(screen.getByText(/^While a game is running: 1 screen, 15 s each/)).toBeInTheDocument();
  });

  it("the In use marker follows the status, not the tab being edited", async () => {
    backend({ screens: SCREENS(), settings: { ingame_enabled: true, ingame: INGAME }, status: { profile: "normal" } });
    render(<ScreensPage />);
    await card("Telemetry");
    expect(tab(/^Not in a game/)).toHaveTextContent("In use");
    fireEvent.click(tab(/^In a game/));
    expect(tab(/^In a game/)).toHaveAttribute("aria-selected", "true");
    expect(tab(/^In a game/)).not.toHaveTextContent("In use");
    expect(tab(/^Not in a game/)).toHaveTextContent("In use");
  });

  it("each list is edited on its own: rotation, interval, add, pin go to the profile of the tab", async () => {
    backend({ screens: SCREENS(), settings: { ingame_enabled: true, ingame: INGAME } });
    render(<ScreensPage />);
    await card("Telemetry");
    fireEvent.click(tab(/^In a game/));
    expect(rows(await region("In a game · in rotation"))).toEqual(["Now Playing"]);
    fireEvent.click((await region("Available")).getByRole("button", { name: "Add Clock" }));
    await waitFor(() => expect(calls("set_rotation")).toEqual([[["now-playing", "clock"], 15, "ingame"]]));
    fireEvent.click(within(await card("Now Playing")).getByRole("button", { name: "More for Now Playing" }));
    fireEvent.click(within(await screen.findByRole("menu")).getByRole("menuitem", { name: "Pin to panel" }));
    await waitFor(() => expect(calls("pin_screen")).toEqual([["now-playing", "ingame"]]));
    fireEvent.change(screen.getByRole("slider", { name: "Seconds per screen" }), { target: { value: "45" } });
    await waitFor(() => expect(calls("set_rotation").at(-1)).toEqual([["now-playing"], 45, "ingame"]));
    fireEvent.click(tab(/^Not in a game/));
    expect(rows(await region("Not in a game · in rotation"))).toEqual(["Telemetry", "Clock"]);
    fireEvent.click(within(await card("Clock")).getByRole("button", { name: "Move Clock up to position 1" }));
    await waitFor(() => expect(calls("set_rotation").at(-1)).toEqual([["clock", "telemetry"], 30]));
  });

  it("the pin badge and unpin are per profile", async () => {
    backend({ screens: SCREENS(), settings: { ingame_enabled: true, ingame: { ...INGAME, pinned: "now-playing" } } });
    render(<ScreensPage />);
    await card("Telemetry");
    expect(within(await card("Telemetry")).queryByText("Pinned")).toBeNull();
    fireEvent.click(tab(/^In a game/));
    expect(within(await card("Now Playing")).getByText("Pinned")).toBeInTheDocument();
    expect(screen.getByText(/Now Playing is pinned, so the panel stays on it while a game is running/)).toBeInTheDocument();
    fireEvent.click(within(await card("Now Playing")).getByRole("button", { name: "More for Now Playing" }));
    fireEvent.click(within(await screen.findByRole("menu")).getByRole("menuitem", { name: "Unpin from panel" }));
    await waitFor(() => expect(calls("unpin_screen")).toEqual([["ingame"]]));
  });

  it("the last screen cannot be removed, in each list", async () => {
    backend({ screens: SCREENS(), settings: { rotation: ["telemetry"], ingame_enabled: true, ingame: INGAME } });
    render(<ScreensPage />);
    fireEvent.keyDown(await card("Telemetry"), { key: "y" });
    expect(await screen.findByText(/Telemetry cannot be removed: the rotation needs at least one screen/)).toBeInTheDocument();
    fireEvent.click(tab(/^In a game/));
    fireEvent.keyDown(await card("Now Playing"), { key: "y" });
    expect(await screen.findByText(/Now Playing cannot be removed: the rotation needs at least one screen/)).toBeInTheDocument();
    expect(calls("set_rotation")).toEqual([]);
  });

  it("Y removes from the edited list only, with Undo in that list", async () => {
    backend({ screens: SCREENS(), settings: { ingame_enabled: true, ingame: { ...INGAME, rotation: ["now-playing", "clock"] } } });
    render(<ScreensPage />);
    await card("Telemetry");
    fireEvent.click(tab(/^In a game/));
    fireEvent.keyDown(await card("Clock"), { key: "y" });
    await waitFor(() => expect(calls("set_rotation")).toEqual([[["now-playing"], 15, "ingame"]]));
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    await waitFor(() => expect(calls("set_rotation").at(-1)).toEqual([["now-playing", "clock"], 15, "ingame"]));
  });

  it("an Undo note does not follow to the other tab", async () => {
    backend({ screens: SCREENS(), settings: { ingame_enabled: true, ingame: { ...INGAME, rotation: ["now-playing", "clock"] } } });
    render(<ScreensPage />);
    await card("Telemetry");
    fireEvent.click(tab(/^In a game/));
    fireEvent.keyDown(await card("Clock"), { key: "y" });
    await screen.findByRole("button", { name: "Undo" });
    fireEvent.click(tab(/^Not in a game/));
    expect(screen.queryByRole("button", { name: "Undo" })).toBeNull();
  });

  it("the panel card says which list is in use", async () => {
    backend({ screens: SCREENS(), settings: { ingame_enabled: true, ingame: INGAME }, status: { profile: "ingame" } });
    render(<ScreensPage />);
    const panel = await region("Panel");
    expect(panel.getByText("Using").nextSibling).toHaveTextContent("In a game");
  });
});

// Tab strip: one tab strip attached to the list, a clear "In use" marker, a heading that names the list, "On panel" only in the list in use.
describe("Screen lists tab strip", () => {
  const on = (status: object = {}) => backend({ screens: SCREENS(), settings: { ingame_enabled: true, ingame: INGAME }, status });

  it("is a tablist of two equal tabs with selected / idle states, inside the list's group", async () => {
    on();
    render(<ScreensPage />);
    await card("Telemetry");
    const list = screen.getByRole("tablist", { name: "Which screens to edit" });
    const tabs = within(list).getAllByRole("tab");
    expect(tabs.map((t) => t.textContent)).toEqual(["Not in a gameIn use", "In a game"]);
    expect(tabs.every((t) => t.classList.contains("ink-tab") && !t.classList.contains("ink-btn"))).toBe(true);
    expect(tabs.map((t) => t.getAttribute("aria-selected"))).toEqual(["true", "false"]);
    fireEvent.click(tabs[1]);
    expect(tabs.map((t) => t.getAttribute("aria-selected"))).toEqual(["false", "true"]);
    // the strip and the heading live in one group, strip first
    const group = screen.getByRole("group", { name: "Screen lists" });
    expect(group).toContainElement(list);
    expect(group).toContainElement(screen.getByRole("heading", { name: "In a game · in rotation" }));
  });

  it("the In use marker follows status.profile, also when it changes", async () => {
    on({ profile: "normal" });
    render(<ScreensPage />);
    await card("Telemetry");
    expect(within(tab(/^Not in a game/)).getByText("In use")).toBeInTheDocument();
    expect(within(tab(/^In a game/)).queryByText("In use")).toBeNull();
    __mock.on("get_status", () => status({ profile: "ingame", current_screen: "now-playing" }));
    __mock.emit("status", status({ profile: "ingame", current_screen: "now-playing" }));
    await waitFor(() => expect(within(tab(/^In a game/)).getByText("In use")).toBeInTheDocument());
    expect(within(tab(/^Not in a game/)).queryByText("In use")).toBeNull();
  });

  it("the heading names the list being edited", async () => {
    on();
    render(<ScreensPage />);
    expect(await screen.findByRole("heading", { name: "Not in a game · in rotation" })).toBeInTheDocument();
    fireEvent.click(tab(/^In a game/));
    expect(screen.getByRole("heading", { name: "In a game · in rotation" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "In rotation" })).toBeNull();
  });

  it("On panel shows only in the list in use (a pinned Telemetry said On panel in the In a game list too)", async () => {
    backend({ screens: SCREENS(), settings: { ingame_enabled: true, ingame: { ...INGAME, rotation: ["telemetry"], pinned: "telemetry" } }, status: { profile: "normal", current_screen: "telemetry" } });
    render(<ScreensPage />);
    expect(within(await card("Telemetry")).getByText("On panel")).toBeInTheDocument();
    fireEvent.click(tab(/^In a game/));
    const t = within(await card("Telemetry"));
    expect(t.queryByText("On panel")).toBeNull();
    expect(t.getByText("Pinned")).toBeInTheDocument(); // the In a game list's own pin is still shown
    expect(screen.queryByText("On panel")).toBeNull(); // nowhere on the page, Available included
  });

  it("toggle off renders no strip and the plain heading", async () => {
    backend({ screens: SCREENS() });
    render(<ScreensPage />);
    expect(await screen.findByRole("heading", { name: "In rotation" })).toBeInTheDocument();
    expect(screen.queryByRole("tablist")).toBeNull();
    expect(screen.queryByText("In use")).toBeNull();
    expect(within(await card("Telemetry")).getByText("On panel")).toBeInTheDocument();
  });
});
