import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SettingsPage } from "../src/pages/SettingsPage";
import { backend } from "./fixtures";
import { __mock } from "./mocks/decky-api";

const sent = () => __mock.callsTo("set_settings").map((a) => a[0]);

describe("SettingsPage panel options", () => {
  it("shows current values", async () => {
    backend({ settings: { idle_timeout_s: 120, ghost_clear_minutes: 30, auto_now_playing: false } });
    render(<SettingsPage />);
    expect(await screen.findByLabelText("Standby timeout (seconds)")).toHaveValue("120");
    expect(screen.getByLabelText("Full refresh to clear ghosting")).toHaveValue("30");
    expect(screen.getByRole("switch", { name: "Switch to Now Playing when a game starts" })).not.toBeChecked();
  });

  it("saves idle timeout, ghost-clear and auto now playing", async () => {
    backend();
    render(<SettingsPage />);
    fireEvent.change(await screen.findByLabelText("Standby timeout (seconds)"), { target: { value: "300" } });
    fireEvent.change(screen.getByLabelText("Full refresh to clear ghosting"), { target: { value: "0" } });
    fireEvent.click(screen.getByRole("switch", { name: "Switch to Now Playing when a game starts" }));
    await waitFor(() => expect(sent()).toEqual([{ idle_timeout_s: 300 }, { ghost_clear_minutes: 0 }, { auto_now_playing: false }]));
  });

  it("offers plain-wording ghost-clear choices, 10 min by default, and never", async () => {
    backend();
    render(<SettingsPage />);
    const sel = await screen.findByLabelText("Full refresh to clear ghosting");
    expect(sel).toHaveValue("10");
    expect(within(sel).getAllByRole("option").map((o) => o.textContent)).toEqual(
      ["Never", "Every 5 min", "Every 10 min (default)", "Every 15 min", "Every 30 min", "Every hour", "Every 2 hours"]);
    fireEvent.change(sel, { target: { value: "120" } });
    await waitFor(() => expect(sent()).toEqual([{ ghost_clear_minutes: 120 }]));
  });

  it("keeps a hand-edited ghost-clear value selectable", async () => {
    backend({ settings: { ghost_clear_minutes: 7 } });
    render(<SettingsPage />);
    const sel = await screen.findByLabelText("Full refresh to clear ghosting");
    expect(sel).toHaveValue("7");
    expect(within(sel).getByRole("option", { name: "Every 7 min" })).toBeInTheDocument();
  });

  it("toggles delete-on-uninstall", async () => {
    backend();
    render(<SettingsPage />);
    fireEvent.click(await screen.findByRole("switch", { name: /Delete my settings and packages/ }));
    await waitFor(() => expect(sent()).toEqual([{ purge_on_uninstall: true }]));
  });

  it("shows a rejected value", async () => {
    backend();
    __mock.on("set_settings", () => ({ ok: false, error: "idle_timeout_s: must be an integer from 5 to 3600" }));
    render(<SettingsPage />);
    fireEvent.change(await screen.findByLabelText("Standby timeout (seconds)"), { target: { value: "10" } });
    expect(await screen.findByText(/must be an integer from 5 to 3600/)).toBeInTheDocument();
  });
});

describe("SettingsPage developer mode", () => {
  it("is off by default, with the warning visible", async () => {
    backend();
    render(<SettingsPage />);
    expect(await screen.findByRole("switch", { name: "Developer mode" })).not.toBeChecked();
    expect(screen.getByText(/not reviewed by anyone|on your own/i)).toBeInTheDocument();
  });

  it("turning it on sends { developer_mode: true }", async () => {
    backend();
    render(<SettingsPage />);
    fireEvent.click(await screen.findByRole("switch", { name: "Developer mode" }));
    await waitFor(() => expect(sent()).toEqual([{ developer_mode: true }]));
  });

  it("reflects developer mode already on", async () => {
    backend({ settings: { developer_mode: true } });
    render(<SettingsPage />);
    expect(await screen.findByRole("switch", { name: "Developer mode" })).toBeChecked();
  });
});

describe("SettingsPage screen catalog", () => {
  it("shows the saved URL and the default as a note", async () => {
    backend({ settings: { catalog_url: "https://example.com/cat/", catalog_url_default: "https://default.example/" } });
    render(<SettingsPage />);
    expect(await screen.findByLabelText("Catalog URL")).toHaveValue("https://example.com/cat/");
    expect(screen.getByText("Default: https://default.example/")).toBeInTheDocument();
  });

  it("Save sends the trimmed URL; Reset sends an empty string", async () => {
    backend({ settings: { catalog_url: "https://example.com/cat/" } });
    render(<SettingsPage />);
    const field = await screen.findByLabelText("Catalog URL");
    fireEvent.change(field, { target: { value: "  https://new.example/x/  " } });
    fireEvent.click(screen.getByText("Save catalog URL"));
    await waitFor(() => expect(sent()).toEqual([{ catalog_url: "https://new.example/x/" }]));
    fireEvent.click(screen.getByText("Reset to default"));
    await waitFor(() => expect(sent()).toEqual([{ catalog_url: "https://new.example/x/" }, { catalog_url: "" }]));
  });

  it("warns client-side about a non-https URL and keeps Save disabled", async () => {
    backend({ settings: { catalog_url: "" } });
    render(<SettingsPage />);
    fireEvent.change(await screen.findByLabelText("Catalog URL"), { target: { value: "http://insecure/" } });
    expect(await screen.findByText(/must start with https:\/\//)).toBeInTheDocument();
    expect(screen.getByText("Save catalog URL")).toBeDisabled();
    expect(sent()).toEqual([]);
  });

  it("shows the backend's rejection", async () => {
    backend({ settings: { catalog_url: "" } });
    __mock.on("set_settings", () => ({ ok: false, error: "catalog_url: a URL must not contain a password" }));
    render(<SettingsPage />);
    fireEvent.change(await screen.findByLabelText("Catalog URL"), { target: { value: "https://ok.example/" } });
    fireEvent.click(screen.getByText("Save catalog URL"));
    expect(await screen.findByText(/must not contain a password/)).toBeInTheDocument();
  });

  it("Reset is disabled when nothing is saved (already on the default)", async () => {
    backend({ settings: { catalog_url: "" } });
    render(<SettingsPage />);
    await screen.findByLabelText("Catalog URL");
    expect(screen.getByText("Reset to default")).toBeDisabled();
  });
});

describe("SettingsPage groups", () => {
  it("keeps the delete-on-uninstall switch out of Connections and Behaviour, in its own set-apart group", async () => {
    backend();
    render(<SettingsPage />);
    const data = within(await screen.findByRole("region", { name: "Data and uninstall" }));
    expect(data.getByRole("switch", { name: /Delete my settings and packages/ })).toBeInTheDocument();
    expect(data.queryByLabelText("Wi-Fi password")).toBeNull();
    for (const g of ["Connections", "Behaviour"]) expect(within(screen.getByRole("region", { name: g })).queryByRole("switch", { name: /Delete my settings/ })).toBeNull();
  });

  it("keeps the behaviour controls out of Connections", async () => {
    backend();
    render(<SettingsPage />);
    const c = within(await screen.findByRole("region", { name: "Connections" }));
    expect(c.queryByLabelText("Standby timeout (seconds)")).toBeNull();
    expect(within(screen.getByRole("region", { name: "Behaviour" })).getByLabelText("Standby timeout (seconds)")).toBeInTheDocument();
  });
});

describe("SettingsPage errors", () => {
  it("backend down", async () => {
    __mock.on("get_settings", () => { throw new Error("Inkterface is not running"); });
    render(<SettingsPage />);
    expect(screen.getByText("Loading…")).toBeInTheDocument();
    expect(await screen.findByRole("alert")).toHaveTextContent("Cannot load settings: Inkterface is not running");
    backend();
    fireEvent.click(screen.getByText("Try again"));
    expect(await screen.findByRole("region", { name: "Behaviour" })).toBeInTheDocument();
  });

  it("surfaces problems found when the files were loaded", async () => {
    backend({ settings: { errors: ["settings.json: rotation: duplicate screen id; using defaults"] } });
    render(<SettingsPage />);
    expect(await screen.findByText(/using defaults/)).toBeInTheDocument();
  });

  it("a failed call shows the transport error", async () => {
    backend();
    __mock.on("set_settings", () => { throw new Error("socket closed"); });
    render(<SettingsPage />);
    fireEvent.click(await screen.findByRole("switch", { name: /Delete my settings/ }));
    expect(await screen.findByText("socket closed")).toBeInTheDocument();
  });

  it("follows settings that change elsewhere", async () => {
    backend({ settings: { idle_timeout_s: 60 } });
    render(<SettingsPage />);
    expect(await screen.findByLabelText("Standby timeout (seconds)")).toHaveValue("60");
    const base = __mock.handlers.get("get_settings")!() as object;
    __mock.on("get_settings", () => ({ ...base, idle_timeout_s: 90 }));
    act(() => __mock.emit("settings_changed"));
    await waitFor(() => expect(screen.getByLabelText("Standby timeout (seconds)")).toHaveValue("90"));
  });
});
