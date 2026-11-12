import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { QuickPanel } from "../src/QuickPanel";
import { PNG, backend, screen as scr, status } from "./fixtures";
import { __mock } from "./mocks/decky-api";
import { Navigation, Router } from "./mocks/decky-ui";

const line = () => screen.findByTestId("status-line");

describe("QuickPanel connection status", () => {
  it("shows connected with the firmware version", async () => {
    backend();
    render(<QuickPanel />);
    expect(await line()).toHaveTextContent("Connected · firmware 1.2.3");
  });

  it("shows loading before the first reply", () => {
    backend();
    render(<QuickPanel />);
    expect(screen.getByTestId("status-line")).toHaveTextContent("Loading…");
  });

  it("shows waiting for panel", async () => {
    backend({ status: { ui_state: "waiting", connected: false, fw_version: null, current_screen: null } });
    render(<QuickPanel />);
    expect(await line()).toHaveTextContent("Waiting for panel");
    expect(screen.queryByRole("img")).toBeNull();
    expect(screen.queryByLabelText("Pin screen")).toBeNull();
  });

  it("shows connecting", async () => {
    backend({ status: { ui_state: "connecting", connected: false } });
    render(<QuickPanel />);
    expect(await line()).toHaveTextContent("Connecting to the panel");
  });

  it("explains firmware too old and shows the backend's reason", async () => {
    backend({ status: { ui_state: "firmware_too_old", connected: false, last_error: "panel speaks protocol v2: update the firmware" } });
    render(<QuickPanel />);
    expect(await line()).toHaveTextContent("Firmware too old");
    expect(screen.getByRole("alert")).toHaveTextContent("update the firmware");
  });

  it("points a too-old firmware at the Firmware page", async () => {
    backend({ status: { ui_state: "firmware_too_old", connected: false, last_error: "protocol v2" } });
    render(<QuickPanel />);
    expect(await line()).toHaveTextContent("open Inkterface → Firmware");
    fireEvent.click(screen.getByText("Update panel firmware"));
    expect(Navigation.Navigate).toHaveBeenCalledWith("/inkterface/firmware");
  });

  it("shows no error once the backend cleared last_error after a reconnect", async () => {
    backend({ status: { ui_state: "connected", connected: true, last_error: null } });
    render(<QuickPanel />);
    expect(await line()).toHaveTextContent("Connected");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("does not offer the firmware button in other states", async () => {
    backend();
    render(<QuickPanel />);
    await line();
    expect(screen.queryByText("Update panel firmware")).toBeNull();
  });

  it("says the panel is being updated", async () => {
    backend({ status: { ui_state: "updating", state: "paused", connected: false } });
    render(<QuickPanel />);
    expect(await line()).toHaveTextContent("Updating the panel firmware");
  });

  it("shows stopped", async () => {
    backend({ status: { ui_state: "stopped", connected: false } });
    render(<QuickPanel />);
    expect(await line()).toHaveTextContent("Stopped");
  });

  it("shows an unknown state verbatim instead of crashing", async () => {
    backend({ status: { ui_state: "weird" as never, connected: false } });
    render(<QuickPanel />);
    expect(await line()).toHaveTextContent("weird");
  });

  it("shows a backend that is not running", async () => {
    __mock.on("get_status", () => { throw new Error("Inkterface is not running"); });
    __mock.on("list_screens", () => { throw new Error("Inkterface is not running"); });
    render(<QuickPanel />);
    await waitFor(() => expect(screen.getByTestId("status-line")).toHaveTextContent("Cannot reach the Inkterface backend: Inkterface is not running"));
  });

  it("follows status events", async () => {
    backend({ status: { ui_state: "waiting", connected: false } });
    render(<QuickPanel />);
    expect(await line()).toHaveTextContent("Waiting");
    act(() => __mock.emit("status", status()));
    expect(await line()).toHaveTextContent("Connected");
    act(() => __mock.emit("status", status({ ui_state: "waiting", connected: false })));
    expect(await line()).toHaveTextContent("Waiting");
  });

  it("recovers from a failed first load when an event arrives", async () => {
    __mock.on("get_status", () => { throw new Error("down"); });
    __mock.on("list_screens", () => []);
    render(<QuickPanel />);
    await waitFor(() => expect(screen.getByTestId("status-line")).toHaveTextContent("Cannot reach"));
    act(() => __mock.emit("status", status()));
    expect(await line()).toHaveTextContent("Connected");
  });

  it("unsubscribes on unmount", async () => {
    backend();
    const { unmount } = render(<QuickPanel />);
    await line();
    unmount();
    expect(__mock.listenerCount("status")).toBe(0);
  });
});

describe("QuickPanel live preview", () => {
  it("renders the current screen as an image", async () => {
    backend();
    render(<QuickPanel />);
    const img = await screen.findByRole("img", { name: "Current screen: telemetry" });
    expect(img).toHaveAttribute("src", PNG);
    expect(__mock.callsTo("preview_screen")).toEqual([["telemetry"]]);
    expect(screen.getByText(/Showing:/)).toHaveTextContent("Showing: Telemetry");
  });

  it("refreshes the image when the panel finishes a frame", async () => {
    backend();
    render(<QuickPanel />);
    await screen.findByRole("img");
    act(() => __mock.emit("status", status({ last_frame_at: 200 })));
    await waitFor(() => expect(__mock.callsTo("preview_screen")).toHaveLength(2));
  });

  it("follows a screen change", async () => {
    backend();
    render(<QuickPanel />);
    await screen.findByRole("img");
    act(() => __mock.emit("status", status({ current_screen: "clock", last_frame_at: 101 })));
    await waitFor(() => expect(__mock.callsTo("preview_screen").at(-1)).toEqual(["clock"]));
  });

  it("shows why there is no preview", async () => {
    backend();
    __mock.on("preview_screen", () => ({ ok: false, error: "render failed" }));
    render(<QuickPanel />);
    expect(await screen.findByText("No preview: render failed")).toBeInTheDocument();
  });

  it("shows a placeholder before the first image and when no screen is current", async () => {
    backend({ status: { current_screen: null } });
    render(<QuickPanel />);
    expect(await screen.findByText("No preview yet")).toBeInTheDocument();
    expect(screen.getByText(/Showing:/)).toHaveTextContent("nothing yet");
    expect(__mock.callsTo("preview_screen")).toEqual([]);
  });

  it("falls back to the screen id while the screen list is unavailable", async () => {
    backend();
    __mock.on("list_screens", () => { throw new Error("nope"); });
    render(<QuickPanel />);
    await waitFor(() => expect(screen.getByText(/Showing:/)).toHaveTextContent("Showing: telemetry"));
    expect(screen.getByLabelText("Pin screen")).toBeDisabled();
  });
});

describe("QuickPanel quick pin", () => {
  it("lists auto plus enabled active screens only", async () => {
    backend({ screens: [scr("telemetry"), scr("off", { enabled: false }), scr("idleonly", { has_active: false }), scr("clock")] });
    render(<QuickPanel />);
    const sel = await screen.findByLabelText("Pin screen");
    await waitFor(() => expect(sel).not.toBeDisabled());
    expect([...(sel as HTMLSelectElement).options].map((o) => o.textContent)).toEqual(["Auto (rotation)", "Telemetry", "Clock"]);
    expect(sel).toHaveValue("__auto__");
  });

  it("pins the chosen screen", async () => {
    backend();
    render(<QuickPanel />);
    const sel = await screen.findByLabelText("Pin screen");
    await waitFor(() => expect(sel).not.toBeDisabled());
    fireEvent.change(sel, { target: { value: "clock" } });
    await waitFor(() => expect(__mock.callsTo("pin_screen")).toEqual([["clock"]]));
  });

  it("choosing auto unpins", async () => {
    backend({ status: { pinned: "clock" } });
    render(<QuickPanel />);
    const sel = await screen.findByLabelText("Pin screen");
    await waitFor(() => expect(sel).toHaveValue("clock"));
    fireEvent.change(sel, { target: { value: "__auto__" } });
    await waitFor(() => expect(__mock.callsTo("unpin_screen")).toHaveLength(1));
    expect(screen.getByText(/\(pinned\)/)).toBeInTheDocument();
  });

  it("shows the backend's refusal", async () => {
    backend();
    __mock.on("pin_screen", () => ({ ok: false, error: "'clock' is not an enabled active screen" }));
    render(<QuickPanel />);
    const sel = await screen.findByLabelText("Pin screen");
    await waitFor(() => expect(sel).not.toBeDisabled());
    fireEvent.change(sel, { target: { value: "clock" } });
    expect(await screen.findByRole("alert")).toHaveTextContent("not an enabled active screen");
  });

  it("shows an error when the backend call itself fails", async () => {
    backend();
    __mock.on("pin_screen", () => { throw new Error("connection lost"); });
    render(<QuickPanel />);
    const sel = await screen.findByLabelText("Pin screen");
    await waitFor(() => expect(sel).not.toBeDisabled());
    fireEvent.change(sel, { target: { value: "clock" } });
    expect(await screen.findByRole("alert")).toHaveTextContent("connection lost");
  });
});

describe("QuickPanel navigation", () => {
  it("closes the side menu and opens the settings route", async () => {
    backend();
    render(<QuickPanel />);
    fireEvent.click(screen.getByText("Open Inkterface settings"));
    expect(Router.CloseSideMenus).toHaveBeenCalledOnce();
    expect(Navigation.Navigate).toHaveBeenCalledWith("/inkterface");
    await line();
  });

  it("still navigates when CloseSideMenus is missing in this Steam build", async () => {
    backend();
    Router.CloseSideMenus.mockImplementation(() => { throw new Error("undefined is not a function"); });
    render(<QuickPanel />);
    fireEvent.click(screen.getByText("Open Inkterface settings"));
    expect(Navigation.Navigate).toHaveBeenCalledWith("/inkterface");
    await line();
  });
});
