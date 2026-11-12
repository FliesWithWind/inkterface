import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AboutPage, LINKS } from "../src/pages/AboutPage";
import { backend } from "./fixtures";
import { __mock } from "./mocks/decky-api";
import { Navigation } from "./mocks/decky-ui";

const value = (label: string) => screen.getByRole("group", { name: label });

describe("AboutPage", () => {
  it("shows plugin, firmware and protocol versions", async () => {
    backend({ status: { plugin_version: "0.3.1", fw_version: "1.2.3", protocol_version: 1, panel_size: [648, 480] } });
    render(<AboutPage />);
    await screen.findByText("0.3.1");
    expect(value("Plugin version")).toHaveTextContent("0.3.1");
    expect(value("Panel firmware")).toHaveTextContent("1.2.3");
    expect(value("Protocol version")).toHaveTextContent("1");
    expect(value("Panel size")).toHaveTextContent("648×480");
  });

  it("copes with a panel that is not connected", async () => {
    backend({ status: { fw_version: null, protocol_version: null, panel_size: null, connected: false } });
    render(<AboutPage />);
    await screen.findByText("0.0.1");
    expect(value("Panel firmware")).toHaveTextContent("not connected");
    expect(value("Protocol version")).toHaveTextContent("unknown");
    expect(value("Panel size")).toHaveTextContent("unknown");
  });

  it("shows placeholders and the error when the backend is down", async () => {
    __mock.on("get_status", () => { throw new Error("down"); });
    render(<AboutPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Cannot reach the backend: down");
    expect(value("Plugin version")).toHaveTextContent("unknown");
  });

  it("opens each link in Steam's browser", async () => {
    backend();
    render(<AboutPage />);
    for (const l of LINKS) fireEvent.click(await screen.findByText(l.label));
    expect(Navigation.NavigateToExternalWeb.mock.calls.map((c) => c[0])).toEqual(LINKS.map((l) => l.url));
    expect(LINKS.every((l) => l.url.startsWith("https://"))).toBe(true);
  });

  it("does not crash when no browser is available", async () => {
    backend();
    Navigation.NavigateToExternalWeb.mockImplementation(() => { throw new Error("no browser"); });
    render(<AboutPage />);
    fireEvent.click(await screen.findByText(LINKS[0].label));
    expect(screen.getByRole("group", { name: "Plugin version" })).toBeInTheDocument();
  });
});
