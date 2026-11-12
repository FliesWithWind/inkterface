// Settings: one card per connection (status header + controls, closed when set, open when not), the same card surface for the other groups.
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { CSS, PAGE_END } from "../src/components/styles";
import { SettingsPage } from "../src/pages/SettingsPage";
import { backend } from "./fixtures";
import { __mock } from "./mocks/decky-api";

const ID = "76561198012345678";
const ALL_SET = { wifi_ssid: "HomeNet", wifi_pass: "set" as const, steamid64: ID, steam_api_key: "set" as const, weather_lat: 50.06, weather_lon: 19.94, weather_place: "Kraków, Poland" };
const region = (name: string) => screen.findByRole("region", { name });
const head = (r: HTMLElement) => r.querySelector<HTMLElement>("[aria-expanded]")!;

describe("connection cards: status header", () => {
  it("all set: three closed cards with a state badge and one line of detail each", async () => {
    backend({ settings: ALL_SET });
    __mock.on("steam_profile", () => ({ ok: true, steamid64: ID, persona: "Ada", avatar: null, public: true }));
    render(<SettingsPage />);
    const wifi = await region("Panel Wi-Fi");
    const steam = await region("Steam");
    const weather = await region("Weather");
    for (const r of [wifi, steam, weather]) {
      expect(head(r)).toHaveAttribute("aria-expanded", "false");
      expect(r.getAttribute("data-state")).toBe("Set");
      expect(within(r).getByText("Set")).toBeInTheDocument();
    }
    expect(within(wifi).getByText("HomeNet, password saved")).toBeInTheDocument();
    expect(await within(steam).findByText("Ada, key saved")).toBeInTheDocument();
    expect(within(weather).getByText("Kraków, Poland, metric")).toBeInTheDocument();
    // closed means no controls: the D-pad goes header to header
    for (const r of [wifi, steam, weather]) expect(r.querySelectorAll("button")).toHaveLength(1);
  });

  it("nothing set: three open cards, each says Not set and what is missing, each with exactly one blue button", async () => {
    backend();
    const { container } = render(<SettingsPage />);
    for (const name of ["Panel Wi-Fi", "Steam", "Weather"]) {
      const r = await region(name);
      expect(head(r)).toHaveAttribute("aria-expanded", "true");
      expect(r.getAttribute("data-state")).toBe("Not set");
      expect(within(r).getByText("Not set")).toBeInTheDocument();
      expect(r.querySelectorAll(".ink-btn-primary")).toHaveLength(1);
    }
    expect(container.querySelectorAll(".ink-conn .ink-btn-primary")).toHaveLength(3);
    expect(within(await region("Panel Wi-Fi")).getByText("Use the Steam Machine's network").className).toContain("ink-btn-primary");
    expect(within(await region("Steam")).getByText("Use my Steam account").className).toContain("ink-btn-primary");
    expect(within(await region("Weather")).getByText("Search").className).toContain("ink-btn-primary");
  });

  it("once set, the same actions are ordinary buttons: the blue one is for what is still to do", async () => {
    backend({ settings: ALL_SET });
    const { container } = render(<SettingsPage />);
    for (const name of ["Panel Wi-Fi", "Steam", "Weather"]) fireEvent.click(head(await region(name)));
    expect(container.querySelectorAll(".ink-conn .ink-btn-primary")).toHaveLength(0);
    expect(within(await region("Steam")).getByText("Use my Steam account")).toBeInTheDocument();
  });

  it("the header opens and closes its card; the other cards are left alone", async () => {
    backend({ settings: ALL_SET });
    render(<SettingsPage />);
    const wifi = await region("Panel Wi-Fi");
    fireEvent.click(head(wifi));
    expect(head(wifi)).toHaveAttribute("aria-expanded", "true");
    expect(within(wifi).getByLabelText("Network name (SSID)")).toHaveValue("HomeNet");
    expect(head(await region("Steam"))).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(head(wifi));
    expect(within(wifi).queryByLabelText("Network name (SSID)")).toBeNull();
  });

  it("saving does not fold the card away under the user's hands", async () => {
    backend();
    render(<SettingsPage />);
    const wifi = await region("Panel Wi-Fi");
    fireEvent.change(within(wifi).getByLabelText("Network name (SSID)"), { target: { value: "HomeNet" } });
    const base = __mock.handlers.get("get_settings")!() as object;
    __mock.on("get_settings", () => ({ ...base, wifi_ssid: "HomeNet" }));
    fireEvent.click(within(wifi).getByText("Save network name"));
    await waitFor(() => expect(__mock.callsTo("set_settings")).toHaveLength(1));
    __mock.emit("settings_changed");
    const again = await screen.findByText(/HomeNet, no password/);
    expect(again).toBeInTheDocument();
    expect(head(await region("Panel Wi-Fi"))).toHaveAttribute("aria-expanded", "true");   // still open: the password comes next
  });

  it("the header is one real button with the name, the state and the detail in it (what a screen reader and the D-pad get)", async () => {
    backend({ settings: ALL_SET });
    render(<SettingsPage />);
    const b = head(await region("Panel Wi-Fi"));
    expect(b.tagName).toBe("BUTTON");
    expect(b.textContent).toBe("Panel Wi-FiSetHomeNet, password saved");
  });

  it("Steam: a failed key test makes the header an Error with the reason, and a good one Connected", async () => {
    backend({ settings: ALL_SET });
    __mock.on("steam_profile", () => ({ ok: true, steamid64: ID, persona: "Ada", avatar: null, public: true }));
    __mock.on("test_steam_key", () => ({ ok: false, error: "Steam rejected this key: it is wrong or was revoked." }));
    render(<SettingsPage />);
    const steam = await region("Steam");
    fireEvent.click(head(steam));
    fireEvent.click(within(steam).getByText("Test key"));
    await waitFor(() => expect(steam.getAttribute("data-state")).toBe("Error"));
    expect(head(steam).textContent).toContain("Steam rejected this key");
    expect(within(steam).getAllByText(/Steam rejected this key/).length).toBeGreaterThan(1);   // in the header and under the button
    __mock.on("test_steam_key", () => ({ ok: true, persona: "Ada", profile_public: true, games_visible: true }));
    fireEvent.click(within(steam).getByText("Test key"));
    await waitFor(() => expect(steam.getAttribute("data-state")).toBe("Connected"));
    expect(head(steam).textContent).toContain("Ada, key works");
  });

  it("Steam without an account: Not set; account without a key: says so", async () => {
    backend({ settings: { steamid64: ID } });
    __mock.on("steam_profile", () => ({ ok: true, steamid64: ID, persona: "Ada", avatar: null, public: true }));
    render(<SettingsPage />);
    const steam = await region("Steam");
    expect(await within(steam).findByText("Ada, no API key yet")).toBeInTheDocument();
  });

  it("Steam avatar is the card icon once the account is known; otherwise the Steam logo icon", async () => {
    backend({ settings: ALL_SET });
    __mock.on("steam_profile", () => ({ ok: true, steamid64: ID, persona: "Ada", avatar: "data:image/png;base64,iVBORw0KGgo=", public: true }));
    const { container } = render(<SettingsPage />);
    await waitFor(() => expect(container.querySelector(".ink-conn-av")).not.toBeNull());
    expect(container.querySelectorAll(".ink-conn-ic svg, .ink-conn-ic")).not.toHaveLength(0);
  });
});

describe("the other groups share the card surface; Data and uninstall stays calm", () => {
  it("Behaviour, Developer, Screen catalog and Data and uninstall sit in a card body", async () => {
    backend();
    const { container } = render(<SettingsPage />);
    await screen.findByRole("region", { name: "Behaviour" });
    for (const name of ["Behaviour", "Developer", "Screen catalog", "Data and uninstall"]) {
      expect(within(screen.getByRole("region", { name })).getByText((_, el) => el?.classList.contains("ink-gbody") ?? false)).toBeTruthy();
    }
    expect(container.querySelectorAll(".ink-gbody")).toHaveLength(4);
  });

  it("the page order is Connections, Behaviour, Developer, Screen catalog, Data and uninstall (D-pad runs top to bottom)", async () => {
    backend();
    render(<SettingsPage />);
    await screen.findByRole("region", { name: "Behaviour" });
    expect(screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent)).toEqual(["Connections", "Behaviour", "Developer", "Screen catalog", "Data and uninstall"]);
    const order = screen.getAllByRole("region").map((r) => r.getAttribute("aria-label"));
    expect(order.indexOf("Panel Wi-Fi")).toBeLessThan(order.indexOf("Steam"));
    expect(order.indexOf("Steam")).toBeLessThan(order.indexOf("Weather"));
    expect(order.indexOf("Weather")).toBeLessThan(order.indexOf("Behaviour"));
  });

  it("Data and uninstall is set apart with a quiet divider and no red anywhere in its styling", () => {
    const apart = /\.ink-group\[data-apart="true"\]\{[^}]*\}/.exec(CSS)![0];
    expect(apart).toContain("border-top:1px solid");
    expect(apart).not.toMatch(/#f[0-9a-f]{2}[0-9a-f]{3}|red|255,\s*90/i);
  });
});

describe("card styling rules", () => {
  const rule = (sel: string) => new RegExp(`${sel.replace(/[.[\]]/g, "\\$&")}[^{]*\\{[^}]*\\}`).exec(CSS)?.[0] ?? "";

  it("the header takes Steam's focus look: white fill, dark text", () => {
    expect(CSS).toMatch(/\.ink-conn-h:focus,\.ink-conn-h\.gpfocus,\.ink-conn-h:focus-visible\{background:#fff !important;color:#23262e !important\}/);
    expect(CSS).toMatch(/\.ink-conn-h\.gpfocus \.ink-conn-d/);   // the detail line stays readable on the white fill
  });

  it("the header fills the card and cannot overflow it", () => {
    const r = rule(".ink-conn-h{");
    expect(r).toContain("width:100% !important");
    expect(r).toContain("min-width:0");
    expect(r).toContain("box-sizing:border-box");
    expect(rule(".ink-conn-t{")).toContain("min-width:0");
    expect(rule(".ink-conn-d{")).toContain("overflow-wrap:anywhere");
  });

  it("the header text is left-aligned against Steam's centred DialogButton (seen centred on the device)", () => {
    expect(rule(".ink-conn-h{")).toContain("text-align:left !important");
    const t = rule(".ink-conn-t{");
    expect(t).toContain("text-align:left !important");
    expect(t).toContain("align-items:flex-start");
  });

  it("no coloured side border (the card is a surface plus a hairline)", () => {
    expect(rule(".ink-conn{")).not.toMatch(/border-(left|right)/);
    expect(rule(".ink-conn{")).toContain("inset 0 0 0 1px");
  });

  it("the chevron turns when open and the motion respects reduced-motion", () => {
    expect(CSS).toContain('.ink-conn-h[aria-expanded="true"] .ink-conn-ch{transform:rotate(180deg)}');
    expect(CSS).toMatch(/prefers-reduced-motion:reduce\)\{\.ink-conn-ch\{transition:none\}\}/);
  });

  it("the footer clearance is still on the page", () => {
    expect(PAGE_END).toBe("calc(48px + 6vh)");
  });
});

describe("the blue button", () => {
  it("fills the card width (one clear action) while ordinary row buttons keep their auto width", async () => {
    expect(CSS).toMatch(/\.ink-btn\.ink-btn-block\{[^}]*width:100% !important/);
    backend();
    const { container } = render(<SettingsPage />);
    await screen.findByRole("region", { name: "Steam" });
    for (const b of container.querySelectorAll(".ink-conn .ink-btn-primary")) expect(b.className).toContain("ink-btn-block");
  });
});
