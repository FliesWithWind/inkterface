import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { SettingsPage } from "../src/pages/SettingsPage";
import { backend } from "./fixtures";
import { __mock } from "./mocks/decky-api";

const sent = () => __mock.callsTo("set_settings").map((a) => a[0]);
const section = async (name: string) => {
  const region = await screen.findByRole("region", { name });
  const head = region.querySelector<HTMLElement>("[aria-expanded]");
  if (head?.getAttribute("aria-expanded") === "false") fireEvent.click(head);   // a set connection starts closed
  return within(region);
};
const KEY = "ABCDEF0123456789ABCDEF0123456789";
const PASS = "hunter2-hunter2";

describe("Settings Connections layout", () => {
  it("holds Wi-Fi, Steam and weather inside the Connections group, before Behaviour and Data and uninstall", async () => {
    backend();
    render(<SettingsPage />);
    const c = await section("Connections");
    for (const name of ["Panel Wi-Fi", "Steam", "Weather"]) expect(c.getByRole("region", { name })).toBeInTheDocument();
    const order = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
    expect(order).toEqual(["Connections", "Behaviour", "Developer", "Screen catalog", "Data and uninstall"]);
  });

  it("backend down: loading, then an error with a retry that recovers", async () => {
    __mock.on("get_settings", () => { throw new Error("Inkterface is not running"); });
    render(<SettingsPage />);
    expect(screen.getByText("Loading…")).toBeInTheDocument();
    expect(await screen.findByRole("alert")).toHaveTextContent("Cannot load settings: Inkterface is not running");
    backend();
    fireEvent.click(screen.getByText("Try again"));
    expect(await section("Panel Wi-Fi")).toBeTruthy();
  });

  it("a failed save shows the backend's reason at the top", async () => {
    backend();
    __mock.on("set_settings", () => ({ ok: false, error: "units: must be 'metric' or 'imperial'" }));
    render(<SettingsPage />);
    fireEvent.change(await screen.findByLabelText("Units"), { target: { value: "imperial" } });
    expect(await screen.findByRole("alert")).toHaveTextContent("units: must be");
  });

  it("follows settings that change elsewhere", async () => {
    backend({ settings: { wifi_ssid: "" } });
    render(<SettingsPage />);
    await screen.findByText(/Without a network the panel cannot fetch/);
    const base = __mock.handlers.get("get_settings")!() as object;
    __mock.on("get_settings", () => ({ ...base, wifi_ssid: "HomeNet", wifi_pass: "set" }));
    act(() => __mock.emit("settings_changed"));
    expect(await screen.findByText("HomeNet, password saved")).toBeInTheDocument();
    expect(screen.getByLabelText("Network name (SSID)")).toHaveValue("HomeNet");
  });
});

describe("Settings Connections Wi-Fi", () => {
  it.each([
    [{ wifi_ssid: "", wifi_pass: "unset" as const }, "Not set", /Without a network the panel cannot fetch/],
    [{ wifi_ssid: "HomeNet", wifi_pass: "set" as const }, "Set", "HomeNet, password saved"],
    [{ wifi_ssid: "Cafe", wifi_pass: "unset" as const }, "Set", "Cafe, no password (right for an open network)"],
  ])("%o: the card header says %s", async (cfg, badge, detail) => {
    backend({ settings: cfg });
    render(<SettingsPage />);
    const w = await section("Panel Wi-Fi");
    expect(w.getAllByText(badge)[0]).toBeInTheDocument();
    expect(w.getByText(detail)).toBeInTheDocument();
  });

  it("saves the network name only when it changed", async () => {
    backend({ settings: { wifi_ssid: "Old" } });
    render(<SettingsPage />);
    const w = await section("Panel Wi-Fi");
    expect(w.getByText("Save network name")).toBeDisabled();
    fireEvent.change(w.getByLabelText("Network name (SSID)"), { target: { value: "HomeNet" } });
    fireEvent.click(w.getByText("Save network name"));
    await waitFor(() => expect(sent()).toEqual([{ wifi_ssid: "HomeNet" }]));
  });

  it("the password is a masked field, empty, with 'not set' in words; save sends it once and empties the field", async () => {
    backend();
    const { container } = render(<SettingsPage />);
    const w = await section("Panel Wi-Fi");
    expect(w.getByRole("status", { name: "Wi-Fi password: not set" })).toBeInTheDocument();
    const input = w.getByLabelText("Wi-Fi password");
    expect(input).toHaveAttribute("type", "password");
    expect(input).toHaveValue("");
    fireEvent.change(input, { target: { value: PASS } });
    fireEvent.click(w.getByText("Save Wi-Fi password"));
    await waitFor(() => expect(sent()).toEqual([{ wifi_pass: PASS }]));
    await waitFor(() => expect(w.getByLabelText("Wi-Fi password")).toHaveValue(""));
    expect(container.innerHTML).not.toContain(PASS);
  });

  it("a saved password is only 'saved': the field is empty, Remove clears it", async () => {
    backend({ settings: { wifi_pass: "set", wifi_ssid: "HomeNet" } });
    const { container } = render(<SettingsPage />);
    const w = await section("Panel Wi-Fi");
    expect(w.getByRole("status", { name: "Wi-Fi password: saved" })).toBeInTheDocument();
    expect(w.getByLabelText("New Wi-Fi password")).toHaveValue("");
    const masked = [...container.querySelectorAll<HTMLInputElement>("input[type=password]")];
    expect(masked.length).toBeGreaterThan(0);
    for (const i of masked) expect(i.value).toBe(""); // no masked field is ever prefilled
    fireEvent.click(w.getByText("Remove Wi-Fi password"));
    await waitFor(() => expect(sent()).toEqual([{ wifi_pass: "" }]));
  });

  it("a password longer than 63 bytes is stopped here and the reason does not repeat it", async () => {
    backend();
    render(<SettingsPage />);
    const w = await section("Panel Wi-Fi");
    const long = "é".repeat(40); // 80 bytes
    fireEvent.change(w.getByLabelText("Wi-Fi password"), { target: { value: long } });
    expect(w.getByRole("alert")).toHaveTextContent("at most 63 bytes");
    expect(w.getByRole("alert")).not.toHaveTextContent(long);
    expect(w.getByText("Save Wi-Fi password")).toBeDisabled();
  });

  it("when the backend refuses the password, the password is not shown in the error", async () => {
    backend();
    __mock.on("set_settings", () => ({ ok: false, error: `wifi_pass: ${PASS} is not allowed` }));
    const { container } = render(<SettingsPage />);
    const w = await section("Panel Wi-Fi");
    fireEvent.change(w.getByLabelText("Wi-Fi password"), { target: { value: PASS } });
    fireEvent.click(w.getByText("Save Wi-Fi password"));
    expect(await w.findByText(/wifi_pass: \*\*\* is not allowed/)).toBeInTheDocument();
    expect(container.innerHTML).not.toContain(PASS);
  });
});

describe("Settings Connections Steam", () => {
  it("never shows the key: only whether one is set", async () => {
    backend({ settings: { steam_api_key: "set" } });
    const { container } = render(<SettingsPage />);
    const s = await section("Steam");
    expect(s.getByRole("status", { name: "Steam Web API key: saved" })).toBeInTheDocument();
    expect(s.getByLabelText("New Steam Web API key")).toHaveValue("");
    expect(container.innerHTML).not.toContain(KEY);
    expect(s.getByText("Remove Steam Web API key")).toBeInTheDocument();
  });

  it("says when no key is set and offers no remove button", async () => {
    backend();
    render(<SettingsPage />);
    const s = await section("Steam");
    expect(s.getByRole("status", { name: "Steam Web API key: not set" })).toBeInTheDocument();
    expect(s.queryByText(/Remove Steam Web API key/)).toBeNull();
    expect(s.getByText("Save Steam Web API key")).toBeDisabled();
  });

  it("sends the trimmed key once, clears the field, and the key is gone from the page and the console", async () => {
    backend();
    const spies = (["log", "info", "warn", "error"] as const).map((m) => vi.spyOn(console, m).mockImplementation(() => undefined));
    const { container } = render(<SettingsPage />);
    const s = await section("Steam");
    const input = s.getByLabelText("Steam Web API key");
    expect(input).toHaveAttribute("type", "password");
    fireEvent.change(input, { target: { value: `  ${KEY} ` } });
    fireEvent.click(s.getByText("Save Steam Web API key"));
    await waitFor(() => expect(sent()).toEqual([{ steam_api_key: KEY }]));
    await waitFor(() => expect(s.getByLabelText("Steam Web API key")).toHaveValue(""));
    expect(container.innerHTML).not.toContain(KEY);
    for (const sp of spies) expect(JSON.stringify(sp.mock.calls)).not.toContain(KEY);
  });

  it("a key of the wrong shape is stopped before it is sent", async () => {
    backend();
    render(<SettingsPage />);
    const s = await section("Steam");
    fireEvent.change(s.getByLabelText("Steam Web API key"), { target: { value: "oops" } });
    expect(s.getByRole("alert")).toHaveTextContent("32 characters");
    expect(s.getByText("Save Steam Web API key")).toBeDisabled();
    expect(sent()).toEqual([]);
  });

  it("removes the key", async () => {
    backend({ settings: { steam_api_key: "set" } });
    render(<SettingsPage />);
    fireEvent.click((await section("Steam")).getByText("Remove Steam Web API key"));
    await waitFor(() => expect(sent()).toEqual([{ steam_api_key: "" }]));
  });

  it("saves a 17-digit SteamID, trimmed, without asking the backend to convert it; unchanged is nothing to save", async () => {
    backend({ settings: { steamid64: "76561198000000000" } });
    render(<SettingsPage />);
    const s = await section("Steam");
    const id = s.getByLabelText("Steam profile link or ID");
    expect(id).toHaveValue("76561198000000000");
    expect(s.getByText("Save SteamID")).toBeDisabled();
    fireEvent.change(id, { target: { value: " 76561198111111111 " } });
    fireEvent.click(s.getByText("Save SteamID"));
    await waitFor(() => expect(sent()).toEqual([{ steamid64: "76561198111111111" }]));
    expect(__mock.callsTo("resolve_steam_id")).toEqual([]);
  });

  it("an empty SteamID clears it", async () => {
    backend({ settings: { steamid64: "76561198000000000" } });
    render(<SettingsPage />);
    const s = await section("Steam");
    fireEvent.change(s.getByLabelText("Steam profile link or ID"), { target: { value: "" } });
    fireEvent.click(s.getByText("Save SteamID"));
    await waitFor(() => expect(sent()).toEqual([{ steamid64: "" }]));
  });
});

describe("Settings Connections weather", () => {
  it("prefills and saves the location as numbers", async () => {
    backend({ settings: { weather_lat: 52.5, weather_lon: 13.4 } });
    render(<SettingsPage />);
    const s = await section("Weather");
    expect(s.getByLabelText("Latitude")).toHaveValue("52.5");
    expect(s.getByText("Save location")).toBeDisabled();
    fireEvent.change(s.getByLabelText("Latitude"), { target: { value: "48.1" } });
    fireEvent.change(s.getByLabelText("Longitude"), { target: { value: "-11.6" } });
    fireEvent.click(s.getByText("Save location"));
    await waitFor(() => expect(sent()).toEqual([{ weather_lat: 48.1, weather_lon: -11.6 }]));
  });

  it("clearing both fields switches weather off", async () => {
    backend({ settings: { weather_lat: 52.5, weather_lon: 13.4 } });
    render(<SettingsPage />);
    const s = await section("Weather");
    fireEvent.change(s.getByLabelText("Latitude"), { target: { value: "" } });
    fireEvent.change(s.getByLabelText("Longitude"), { target: { value: " " } });
    fireEvent.click(s.getByText("Save location"));
    await waitFor(() => expect(sent()).toEqual([{ weather_lat: null, weather_lon: null }]));
  });

  it("junk coordinates are rejected by the backend, with its message", async () => {
    backend();
    __mock.on("set_settings", (p: { weather_lat: number }) => ({ ok: false, error: Number.isNaN(p.weather_lat) ? "weather_lat: must be a number from -90 to 90" : "?" }));
    render(<SettingsPage />);
    const s = await section("Weather");
    fireEvent.click(s.getByRole("switch", { name: "Advanced: type coordinates" }));
    fireEvent.change(s.getByLabelText("Latitude"), { target: { value: "abc" } });
    fireEvent.change(s.getByLabelText("Longitude"), { target: { value: "1" } });
    fireEvent.click(s.getByText("Save location"));
    expect(await screen.findByText(/weather_lat: must be a number/)).toBeInTheDocument();
  });

  it("changes units", async () => {
    backend();
    render(<SettingsPage />);
    fireEvent.change(await screen.findByLabelText("Units"), { target: { value: "imperial" } });
    await waitFor(() => expect(sent()).toEqual([{ units: "imperial" }]));
  });
});
