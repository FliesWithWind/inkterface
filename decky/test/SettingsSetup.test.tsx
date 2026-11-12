// Settings > Connections made easy: place search, IP location, "Use my Steam account", pasted links, the API key (QR, test), the machine's Wi-Fi.
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
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
const ID = "76561198012345678";
const KRAKOW = { label: "Kraków, Lesser Poland, Poland", name: "Kraków, Poland", lat: 50.0614, lon: 19.9366, country_code: "PL" };
const BERLIN = { label: "Berlin, Germany", name: "Berlin, Germany", lat: 52.52, lon: 13.405, country_code: "DE" };

function locale(l: string) {
  vi.spyOn(navigator, "language", "get").mockReturnValue(l);
}
const g = globalThis as { App?: unknown; SteamClient?: unknown };
beforeEach(() => {
  delete g.App;
  delete g.SteamClient;
});
afterEach(() => {
  delete g.App;
  delete g.SteamClient;
});

describe("Weather: search by name", () => {
  it("shows the chosen place by name, not as coordinates", async () => {
    backend({ settings: { weather_lat: 50.0614, weather_lon: 19.9366, weather_place: "Kraków, Poland" } });
    render(<SettingsPage />);
    const w = await section("Weather");
    expect(w.getByText("Kraków, Poland, metric")).toBeInTheDocument();
    expect(w.queryByText(/50\.0614/)).toBeNull();
    expect(w.queryByLabelText("Latitude")).toBeNull();   // the coordinates are an advanced option
  });

  it.each([
    [{}, "Not set", "Weather is off. Search for your town to switch it on."],
    [{ weather_lat: 52.5, weather_lon: 13.4 }, "Set", "custom coordinates 52.5, 13.4, metric"],
    [{ weather_lat: 40.7, weather_lon: -74, weather_place: "New York, United States", units: "imperial" as const }, "Set", "New York, United States, imperial"],
  ])("%o: the header says %s, %s", async (cfg, badge, detail) => {
    backend({ settings: cfg });
    render(<SettingsPage />);
    const w = await section("Weather");
    expect(w.getByText(badge)).toBeInTheDocument();
    expect(w.getByText(detail)).toBeInTheDocument();
  });

  it("Search needs two letters, sends the query with the system language, and lists what it finds", async () => {
    locale("pl-PL");
    backend();
    __mock.on("search_places", () => ({ ok: true, results: [KRAKOW, { ...KRAKOW, label: "Krakow, New York, United States", name: "Krakow, United States", lat: 44.7, lon: -75.1 }] }));
    render(<SettingsPage />);
    const w = await section("Weather");
    expect(w.getByText("Search")).toBeDisabled();
    fireEvent.change(w.getByLabelText("Town or place name"), { target: { value: "K" } });
    expect(w.getByText("Search")).toBeDisabled();
    fireEvent.change(w.getByLabelText("Town or place name"), { target: { value: "  Kraków " } });
    fireEvent.click(w.getByText("Search"));
    expect(await w.findByText("Kraków, Lesser Poland, Poland")).toBeInTheDocument();
    expect(w.getByText("Krakow, New York, United States")).toBeInTheDocument();
    expect(__mock.callsTo("search_places")).toEqual([["Kraków", "pl"]]);
    expect(sent()).toEqual([]);   // nothing is saved until one is chosen
  });

  it("choosing a place saves coordinates and the name; the first place also sets units from the system locale", async () => {
    locale("en-US");
    backend();
    __mock.on("search_places", () => ({ ok: true, results: [KRAKOW] }));
    render(<SettingsPage />);
    const w = await section("Weather");
    fireEvent.change(w.getByLabelText("Town or place name"), { target: { value: "Krakow" } });
    fireEvent.click(w.getByText("Search"));
    fireEvent.click(await w.findByText("Kraków, Lesser Poland, Poland"));
    await waitFor(() => expect(sent()).toEqual([{ weather_lat: 50.0614, weather_lon: 19.9366, weather_place: "Kraków, Poland", units: "imperial" }]));
  });

  it("a European locale keeps metric; a later pick does not touch the units", async () => {
    locale("pl-PL");
    backend({ settings: { weather_lat: 1, weather_lon: 2, weather_place: "Old, Place", units: "imperial" } });
    __mock.on("search_places", () => ({ ok: true, results: [BERLIN] }));
    render(<SettingsPage />);
    const w = await section("Weather");
    fireEvent.change(w.getByLabelText("Town or place name"), { target: { value: "Berlin" } });
    fireEvent.click(w.getByText("Search"));
    fireEvent.click(await w.findByText("Berlin, Germany"));
    await waitFor(() => expect(sent()).toEqual([{ weather_lat: 52.52, weather_lon: 13.405, weather_place: "Berlin, Germany" }]));
    cleanupLocale();
  });

  it("first pick with a metric locale sends metric", async () => {
    locale("de-DE");
    backend();
    __mock.on("search_places", () => ({ ok: true, results: [BERLIN] }));
    render(<SettingsPage />);
    const w = await section("Weather");
    fireEvent.change(w.getByLabelText("Town or place name"), { target: { value: "Berlin" } });
    fireEvent.click(w.getByText("Search"));
    fireEvent.click(await w.findByText("Berlin, Germany"));
    await waitFor(() => expect(sent()).toEqual([{ weather_lat: 52.52, weather_lon: 13.405, weather_place: "Berlin, Germany", units: "metric" }]));
  });

  it("no match and network errors are shown in words and save nothing", async () => {
    backend();
    __mock.on("search_places", () => ({ ok: false, error: "No place found with that name. Try the English spelling or a bigger town nearby." }));
    render(<SettingsPage />);
    const w = await section("Weather");
    fireEvent.change(w.getByLabelText("Town or place name"), { target: { value: "Nowhereville" } });
    fireEvent.click(w.getByText("Search"));
    expect(await w.findByRole("alert")).toHaveTextContent("No place found");
    expect(sent()).toEqual([]);
  });

  it("a failed save of the chosen place shows the reason and keeps the list", async () => {
    backend();
    __mock.on("search_places", () => ({ ok: true, results: [KRAKOW] }));
    __mock.on("set_settings", () => ({ ok: false, error: "weather_place: must be text of at most 120 characters" }));
    render(<SettingsPage />);
    const w = await section("Weather");
    fireEvent.change(w.getByLabelText("Town or place name"), { target: { value: "Krakow" } });
    fireEvent.click(w.getByText("Search"));
    fireEvent.click(await w.findByText("Kraków, Lesser Poland, Poland"));
    expect(await screen.findByText(/weather_place: must be text/)).toBeInTheDocument();
    expect(w.getByText("Kraków, Lesser Poland, Poland")).toBeInTheDocument();
  });

  it("a new search replaces the old list; Search is off while it runs", async () => {
    backend();
    let release: (v: unknown) => void = () => undefined;
    __mock.on("search_places", () => new Promise((r) => { release = r; }));
    render(<SettingsPage />);
    const w = await section("Weather");
    fireEvent.change(w.getByLabelText("Town or place name"), { target: { value: "Krakow" } });
    fireEvent.click(w.getByText("Search"));
    await waitFor(() => expect(w.getByText("Search")).toBeDisabled());
    expect(w.getByText("Use approximate location")).toBeDisabled();
    release({ ok: true, results: [KRAKOW] });
    expect(await w.findByText("Kraków, Lesser Poland, Poland")).toBeInTheDocument();
    expect(w.getByText("Search")).not.toBeDisabled();
  });

  it("Remove location switches weather off", async () => {
    backend({ settings: { weather_lat: 50.06, weather_lon: 19.94, weather_place: "Kraków, Poland" } });
    render(<SettingsPage />);
    const w = await section("Weather");
    fireEvent.click(w.getByText("Remove location"));
    await waitFor(() => expect(sent()).toEqual([{ weather_lat: null, weather_lon: null }]));
  });

  it("no Remove button when there is no location", async () => {
    backend();
    render(<SettingsPage />);
    expect((await section("Weather")).queryByText("Remove location")).toBeNull();
  });
});

function cleanupLocale() {
  vi.restoreAllMocks();
}

describe("Weather: approximate location", () => {
  it("makes no request until the button is pressed, and says what is sent and to whom", async () => {
    backend();
    render(<SettingsPage />);
    const w = await section("Weather");
    expect(w.getByText(/ipwho\.is/)).toHaveTextContent("Only its IP address is sent");
    expect(__mock.callsTo("locate_by_ip")).toEqual([]);
  });

  it("offers the result for confirmation instead of saving it", async () => {
    locale("pl-PL");
    backend();
    __mock.on("locate_by_ip", () => ({ ok: true, name: "Warsaw, Poland", lat: 52.23, lon: 21.01, country_code: "PL", provider: "ipwho.is" }));
    render(<SettingsPage />);
    const w = await section("Weather");
    fireEvent.click(w.getByText("Use approximate location"));
    expect(await w.findByText("Warsaw, Poland (approximate)")).toBeInTheDocument();
    expect(w.getByText(/Approximate location from ipwho\.is/)).toBeInTheDocument();
    expect(sent()).toEqual([]);
    fireEvent.click(w.getByText("Warsaw, Poland (approximate)"));
    await waitFor(() => expect(sent()).toEqual([{ weather_lat: 52.23, weather_lon: 21.01, weather_place: "Warsaw, Poland", units: "metric" }]));
  });

  it("an IP lookup that fails says why and saves nothing", async () => {
    backend();
    __mock.on("locate_by_ip", () => ({ ok: false, error: "Cannot reach ipwho.is: URLError. Check the Steam Machine's network." }));
    render(<SettingsPage />);
    const w = await section("Weather");
    fireEvent.click(w.getByText("Use approximate location"));
    expect(await w.findByRole("alert")).toHaveTextContent("Cannot reach ipwho.is");
    expect(sent()).toEqual([]);
  });
});

describe("Weather: coordinates are an advanced option", () => {
  it("hidden by default, shown by the toggle, saved as numbers; saving by hand drops the place name on the backend", async () => {
    backend({ settings: { weather_lat: 50.06, weather_lon: 19.94, weather_place: "Kraków, Poland" } });
    render(<SettingsPage />);
    const w = await section("Weather");
    expect(w.queryByLabelText("Latitude")).toBeNull();
    fireEvent.click(w.getByRole("switch", { name: "Advanced: type coordinates" }));
    expect(w.getByLabelText("Latitude")).toHaveValue("50.06");
    fireEvent.change(w.getByLabelText("Latitude"), { target: { value: "48.1" } });
    fireEvent.click(w.getByText("Save location"));
    await waitFor(() => expect(sent()).toEqual([{ weather_lat: 48.1, weather_lon: 19.94 }]));   // no weather_place key: the backend clears it
  });

  it("open at once for coordinates that have no place name", async () => {
    backend({ settings: { weather_lat: 52.5, weather_lon: 13.4 } });
    render(<SettingsPage />);
    expect((await section("Weather")).getByLabelText("Latitude")).toHaveValue("52.5");
  });
});

describe("Steam: Use my Steam account", () => {
  it("fills in and saves the signed-in account from App.m_CurrentUser, then shows who it is", async () => {
    g.App = { m_CurrentUser: { strSteamID: ID } };
    backend();
    __mock.on("steam_profile", () => ({ ok: true, steamid64: ID, persona: "Ada", avatar: "data:image/png;base64,iVBORw0KGgo=", public: true }));
    render(<SettingsPage />);
    const s = await section("Steam");
    expect(s.getByText("Not set")).toBeInTheDocument();
    fireEvent.click(s.getByText("Use my Steam account"));
    await waitFor(() => expect(sent()).toEqual([{ steamid64: ID }]));
    expect(s.getByLabelText("Steam profile link or ID")).toHaveValue(ID);
  });

  it("shows the persona name and avatar once the account is saved", async () => {
    backend({ settings: { steamid64: ID } });
    __mock.on("steam_profile", () => ({ ok: true, steamid64: ID, persona: "Ada", avatar: "data:image/png;base64,iVBORw0KGgo=", public: true }));
    const { container } = render(<SettingsPage />);
    const s = await section("Steam");
    expect(await s.findByRole("status", { name: "Steam account: Ada" })).toHaveTextContent(`SteamID64 ${ID}`);
    expect(container.querySelector("img.ink-conn-av")).toHaveAttribute("src", "data:image/png;base64,iVBORw0KGgo=");   // the avatar is the card icon
    expect(s.getByText("Ada, no API key yet")).toBeInTheDocument();
    expect(__mock.callsTo("steam_profile")).toEqual([[ID]]);
  });

  it("without an avatar the name still shows; a private profile is said", async () => {
    backend({ settings: { steamid64: ID } });
    __mock.on("steam_profile", () => ({ ok: true, steamid64: ID, persona: "Ada", avatar: null, public: false }));
    render(<SettingsPage />);
    expect(await (await section("Steam")).findByRole("status", { name: "Steam account: Ada" })).toHaveTextContent("profile is private");
  });

  it("when the profile cannot be fetched the saved id is still shown, with the reason", async () => {
    backend({ settings: { steamid64: ID } });
    __mock.on("steam_profile", () => ({ ok: false, error: "Cannot reach Steam: URLError. Check the Steam Machine's network." }));
    render(<SettingsPage />);
    const s = await section("Steam");
    expect(await s.findByText(/The profile could not be looked up: Cannot reach Steam/)).toBeInTheDocument();
    expect(s.getByText("Account saved, no API key yet")).toBeInTheDocument();
  });

  it("falls back to SteamClient.User when App is not there", async () => {
    g.SteamClient = { User: { RegisterForCurrentUserChanges: (cb: (u: unknown) => void) => { queueMicrotask(() => cb({ strSteamID: ID })); return { unregister: vi.fn() }; } } };
    backend();
    render(<SettingsPage />);
    fireEvent.click((await section("Steam")).getByText("Use my Steam account"));
    await waitFor(() => expect(sent()).toEqual([{ steamid64: ID }]));
  });

  it("when Steam does not say who is signed in: a message, nothing saved", async () => {
    backend();
    render(<SettingsPage />);
    const s = await section("Steam");
    fireEvent.click(s.getByText("Use my Steam account"));
    expect(await s.findByRole("alert")).toHaveTextContent("Paste your profile link");
    expect(sent()).toEqual([]);
  });
});

describe("Steam: paste a link, SteamID3 or custom name", () => {
  it.each(["https://steamcommunity.com/profiles/" + ID, "[U:1:52079950]", "52079950", "https://steamcommunity.com/id/some_name"])("%s is converted by the backend, then saved as a SteamID64", async (text) => {
    backend();
    __mock.on("resolve_steam_id", () => ({ ok: true, steamid64: ID }));
    render(<SettingsPage />);
    const s = await section("Steam");
    fireEvent.change(s.getByLabelText("Steam profile link or ID"), { target: { value: `  ${text} ` } });
    fireEvent.click(s.getByText("Save SteamID"));
    await waitFor(() => expect(sent()).toEqual([{ steamid64: ID }]));
    expect(__mock.callsTo("resolve_steam_id")).toEqual([[text]]);
    expect(s.getByLabelText("Steam profile link or ID")).toHaveValue(ID);
  });

  it("a custom name without an API key gets the backend's explanation and nothing is saved", async () => {
    backend();
    __mock.on("resolve_steam_id", () => ({ ok: false, error: "A custom profile name can only be looked up with a Steam Web API key." }));
    render(<SettingsPage />);
    const s = await section("Steam");
    fireEvent.change(s.getByLabelText("Steam profile link or ID"), { target: { value: "some_name" } });
    fireEvent.click(s.getByText("Save SteamID"));
    expect(await s.findByText(/can only be looked up with a Steam Web API key/)).toBeInTheDocument();
    expect(sent()).toEqual([]);
  });

  it("garbage is refused by the backend and not saved", async () => {
    backend();
    __mock.on("resolve_steam_id", () => ({ ok: false, error: "That is not a Steam account." }));
    render(<SettingsPage />);
    const s = await section("Steam");
    fireEvent.change(s.getByLabelText("Steam profile link or ID"), { target: { value: "!!" } });
    fireEvent.click(s.getByText("Save SteamID"));
    expect(await s.findByRole("alert")).toHaveTextContent("not a Steam account");
    expect(sent()).toEqual([]);
  });

  it("says what is accepted", async () => {
    backend();
    render(<SettingsPage />);
    expect((await section("Steam")).getByText(/Paste your profile link, your 17-digit SteamID64, a SteamID3/)).toBeInTheDocument();
  });
});

describe("Steam Web API key: where to get it, QR code, test", () => {
  it("tells where to get a key and draws a QR code of that page", async () => {
    backend();
    const { container } = render(<SettingsPage />);
    const s = await section("Steam");
    expect(s.getByText(/steamcommunity\.com\/dev\/apikey \(type any domain name/)).toBeInTheDocument();
    const svg = container.querySelector("svg[role=img]")!;
    expect(svg).toHaveAttribute("aria-label", "QR code for steamcommunity.com/dev/apikey");
    expect(svg.querySelector("path")!.getAttribute("d")!.length).toBeGreaterThan(500);
    expect(svg.querySelector("rect")).toHaveAttribute("fill", "#fff");   // dark on white, whatever the theme
  });

  it("Test key is off with a reason until a key and an account are saved", async () => {
    backend();
    render(<SettingsPage />);
    let s = await section("Steam");
    expect(s.getByText("Test key")).toBeDisabled();
    expect(s.getByText("Save a key to test it.")).toBeInTheDocument();
  });

  it("a key without an account: asks for the account first", async () => {
    backend({ settings: { steam_api_key: "set" } });
    render(<SettingsPage />);
    const s = await section("Steam");
    expect(s.getByText("Test key")).toBeDisabled();
    expect(s.getByText(/Save your SteamID first/)).toBeInTheDocument();
  });

  it("success: 'Works: <name>' and the request carries no key from the UI", async () => {
    backend({ settings: { steam_api_key: "set", steamid64: ID } });
    __mock.on("test_steam_key", () => ({ ok: true, persona: "Ada", profile_public: true, games_visible: true }));
    const { container } = render(<SettingsPage />);
    const s = await section("Steam");
    fireEvent.click(s.getByText("Test key"));
    expect(await s.findByRole("status", { name: "Works: Ada" })).toBeInTheDocument();
    expect(__mock.callsTo("test_steam_key")).toEqual([[]]);
    expect(s.queryByText("Game details are private")).toBeNull();
    expect(container.innerHTML).not.toContain(KEY);
  });

  it("works but game details are private: says how to fix it", async () => {
    backend({ settings: { steam_api_key: "set", steamid64: ID } });
    __mock.on("test_steam_key", () => ({ ok: true, persona: "Ada", profile_public: true, games_visible: false }));
    render(<SettingsPage />);
    const s = await section("Steam");
    fireEvent.click(s.getByText("Test key"));
    expect(await s.findByRole("status", { name: "Works: Ada" })).toBeInTheDocument();
    expect(s.getByRole("status", { name: "Game details are private" })).toHaveTextContent("until Game details is Public");
  });

  it("works but the whole profile is private", async () => {
    backend({ settings: { steam_api_key: "set", steamid64: ID } });
    __mock.on("test_steam_key", () => ({ ok: true, persona: "Ada", profile_public: false, games_visible: false }));
    render(<SettingsPage />);
    const s = await section("Steam");
    fireEvent.click(s.getByText("Test key"));
    expect(await s.findByRole("status", { name: "Game details are private" })).toHaveTextContent("profile is private");
  });

  it("unknown visibility (the games check failed) shows no warning", async () => {
    backend({ settings: { steam_api_key: "set", steamid64: ID } });
    __mock.on("test_steam_key", () => ({ ok: true, persona: "Ada", profile_public: true, games_visible: null }));
    render(<SettingsPage />);
    const s = await section("Steam");
    fireEvent.click(s.getByText("Test key"));
    await s.findByRole("status", { name: "Works: Ada" });
    expect(s.queryByText("Game details are private")).toBeNull();
  });

  it.each([
    "Steam rejected this key: it is wrong or was revoked. Make a new one at steamcommunity.com/dev/apikey.",
    "Cannot reach Steam: URLError. Check the Steam Machine's network.",
    "The key works, but Steam knows no profile with that SteamID64. Check the 17 digits.",
  ])("a failure is shown as it is: %s", async (error) => {
    backend({ settings: { steam_api_key: "set", steamid64: ID } });
    __mock.on("test_steam_key", () => ({ ok: false, error }));
    render(<SettingsPage />);
    const s = await section("Steam");
    fireEvent.click(s.getByText("Test key"));
    expect(await s.findByRole("alert")).toHaveTextContent(error);
    expect(s.queryByText(/Works:/)).toBeNull();
  });

  it("shows Testing… and cannot be pressed twice; a new result replaces the old one", async () => {
    backend({ settings: { steam_api_key: "set", steamid64: ID } });
    let release: (v: unknown) => void = () => undefined;
    __mock.on("test_steam_key", () => new Promise((r) => { release = r; }));
    render(<SettingsPage />);
    const s = await section("Steam");
    fireEvent.click(s.getByText("Test key"));
    expect(await s.findByText("Testing…")).toBeDisabled();
    release({ ok: false, error: "Steam rejected this key" });
    expect(await s.findByRole("alert")).toBeInTheDocument();
    __mock.on("test_steam_key", () => ({ ok: true, persona: "Ada", profile_public: true, games_visible: true }));
    fireEvent.click(s.getByText("Test key"));
    expect(await s.findByRole("status", { name: "Works: Ada" })).toBeInTheDocument();
    expect(s.queryByRole("alert")).toBeNull();
  });

  it("a backend that is gone becomes a message, not a crash", async () => {
    backend({ settings: { steam_api_key: "set", steamid64: ID } });
    __mock.on("test_steam_key", () => { throw new Error("Inkterface is not running"); });
    render(<SettingsPage />);
    const s = await section("Steam");
    fireEvent.click(s.getByText("Test key"));
    expect(await s.findByRole("alert")).toHaveTextContent("Inkterface is not running");
  });

  it("the key typed into the field never reaches the page, the test or the console", async () => {
    backend({ settings: { steamid64: ID } });
    const spies = (["log", "info", "warn", "error"] as const).map((m) => vi.spyOn(console, m).mockImplementation(() => undefined));
    __mock.on("set_settings", () => ({ ok: true }));
    __mock.on("test_steam_key", () => ({ ok: false, error: "Steam rejected this key" }));
    const { container } = render(<SettingsPage />);
    const s = await section("Steam");
    fireEvent.change(s.getByLabelText("Steam Web API key"), { target: { value: KEY } });
    fireEvent.click(s.getByText("Save Steam Web API key"));
    await waitFor(() => expect(sent()).toEqual([{ steam_api_key: KEY }]));
    expect(container.innerHTML).not.toContain(KEY);
    for (const sp of spies) expect(JSON.stringify(sp.mock.calls)).not.toContain(KEY);
    expect(__mock.callsTo("test_steam_key").flat()).toEqual([]);
  });

  it("says that game details must be public for Steam Stats", async () => {
    backend();
    render(<SettingsPage />);
    expect((await section("Steam")).getByText(/For Steam Stats, set Game details to Public/)).toBeInTheDocument();
  });
});

describe("Panel Wi-Fi: use the Steam Machine's network", () => {
  it("fills the name from the active network without saving it, and says the password must be typed", async () => {
    backend();
    __mock.on("wifi_current", () => ({ ok: true, ssid: "HomeNet", saved: [] }));
    render(<SettingsPage />);
    const w = await section("Panel Wi-Fi");
    expect(w.getByText(/cannot be copied: this machine keeps it private/)).toBeInTheDocument();
    expect(w.getByText(/only joins 2\.4 GHz/)).toBeInTheDocument();
    fireEvent.click(w.getByText("Use the Steam Machine's network"));
    expect(await w.findByText(/Filled in “HomeNet”/)).toBeInTheDocument();
    expect(w.getByLabelText("Network name (SSID)")).toHaveValue("HomeNet");
    expect(sent()).toEqual([]);                                   // pre-filled only
    expect(w.getByLabelText("Wi-Fi password")).toHaveValue("");   // never filled
    fireEvent.click(w.getByText("Save network name"));
    await waitFor(() => expect(sent()).toEqual([{ wifi_ssid: "HomeNet" }]));
  });

  it("on a cable it offers the remembered Wi-Fi networks to pick from", async () => {
    backend();
    __mock.on("wifi_current", () => ({ ok: true, ssid: null, saved: ["HomeNet", "Office"] }));
    render(<SettingsPage />);
    const w = await section("Panel Wi-Fi");
    fireEvent.click(w.getByText("Use the Steam Machine's network"));
    expect(await w.findByText(/on a cable right now/)).toBeInTheDocument();
    fireEvent.click(w.getByText("Office"));
    expect(w.getByLabelText("Network name (SSID)")).toHaveValue("Office");
    expect(w.queryByText("HomeNet")).toBeNull();   // the list is gone once one is chosen
    expect(sent()).toEqual([]);
  });

  it("no NetworkManager or nothing to copy: the reason, and the field is left alone", async () => {
    backend({ settings: { wifi_ssid: "Mine" } });
    __mock.on("wifi_current", () => ({ ok: false, error: "This machine has no NetworkManager (nmcli), so its Wi-Fi cannot be read. Type the network name." }));
    render(<SettingsPage />);
    const w = await section("Panel Wi-Fi");
    fireEvent.click(w.getByText("Use the Steam Machine's network"));
    expect(await w.findByRole("alert")).toHaveTextContent("no NetworkManager");
    expect(w.getByLabelText("Network name (SSID)")).toHaveValue("Mine");
  });

  it("is off while it reads", async () => {
    backend();
    let release: (v: unknown) => void = () => undefined;
    __mock.on("wifi_current", () => new Promise((r) => { release = r; }));
    render(<SettingsPage />);
    const w = await section("Panel Wi-Fi");
    fireEvent.click(w.getByText("Use the Steam Machine's network"));
    expect(await w.findByText("Reading the network…")).toBeDisabled();
    release({ ok: true, ssid: "HomeNet", saved: [] });
    await w.findByText(/Filled in/);
  });

  it("a second press replaces the old note", async () => {
    backend();
    __mock.on("wifi_current", () => ({ ok: true, ssid: "A", saved: [] }));
    render(<SettingsPage />);
    const w = await section("Panel Wi-Fi");
    fireEvent.click(w.getByText("Use the Steam Machine's network"));
    await w.findByText(/Filled in “A”/);
    __mock.on("wifi_current", () => ({ ok: false, error: "no" }));
    fireEvent.click(w.getByText("Use the Steam Machine's network"));
    await w.findByRole("alert");
    expect(w.queryByText(/Filled in/)).toBeNull();
  });
});
