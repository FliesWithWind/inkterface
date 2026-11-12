// Regression tests for what a Steam Machine test unit showed: focus lost after a move, rows that were focus stops of
// their own, no duplicate page titles, the status card and button CSS. The device facts these encode were read over CDP.
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { CSS } from "../src/components/styles";
import { OrderedList, Slots } from "../src/components/ScreenList";
import { IdlePage } from "../src/pages/IdlePage";
import { ScreensPage } from "../src/pages/ScreensPage";
import { StorePage } from "../src/pages/StorePage";
import { backend, screen as info } from "./fixtures";

const NAMES = ["Telemetry", "Now Playing", "Thermals", "Weather"];
const items = NAMES.map((n) => info(n.toLowerCase().replace(" ", "-"), { name: n, description: `${n} desc` }));
const slots: Slots = { thumb: (s) => <div>pic {s.name}</div>, sheet: (s) => (s.name === "Weather" ? null : <div>settings body of {s.name}</div>) };

const rowNames = () => screen.getAllByRole("group").filter((g) => g.hasAttribute("data-row-id")).map((g) => g.getAttribute("aria-label"));
const row = (name: string) => within(screen.getByRole("group", { name }));
const pad = (el: Element, key: string) => fireEvent.keyDown(el, { key });
const settings = (name: string) => row(name).getByRole("button", { name: new RegExp(`^Settings for ${name}`) });
const down = (name: string) => row(name).getByRole("button", { name: new RegExp(`^Move ${name} down`) });
const up = (name: string) => row(name).getByRole("button", { name: new RegExp(`^Move ${name} up`) });
/** Steam's gamepad focus is the element with the `gpfocus` class; it is not document.activeElement (on the device that stayed on <body>). */
const steamFocus = (el: HTMLElement) => {
  (document.activeElement as HTMLElement | null)?.blur();
  document.querySelectorAll(".gpfocus").forEach((x) => x.classList.remove("gpfocus")); // Steam keeps exactly one
  el.classList.add("gpfocus");
};

function Harness() {
  const [list, setList] = useState(items);
  return (
    <OrderedList items={list} slots={slots} empty="nothing" removeLabel="Remove"
      onChange={(ids) => { setList(ids.map((id) => list.find((x) => x.id === id)!)); return { ok: true }; }} />
  );
}

describe("focus after a move (device: gpfocus vanished, the next D-pad press landed in the sidebar)", () => {
  it("R1 moves the row and focus stays on the same control of that row, even when document.activeElement is <body>", async () => {
    render(<Harness />);
    steamFocus(settings("Now Playing"));
    expect(document.activeElement).toBe(document.body);
    pad(settings("Now Playing"), "PageDown");
    await waitFor(() => expect(rowNames()).toEqual(["Telemetry", "Thermals", "Now Playing", "Weather"]));
    expect(document.activeElement).toBe(settings("Now Playing"));
  });

  it("L1 does the same upwards; a disabled Settings button (screen without a sheet) hands focus to More", async () => {
    render(<Harness />);
    steamFocus(settings("Weather"));
    pad(settings("Weather"), "PageUp");
    await waitFor(() => expect(rowNames()).toEqual(["Telemetry", "Now Playing", "Weather", "Thermals"]));
    expect(document.activeElement).toBe(row("Weather").getByRole("button", { name: "More for Weather" }));
  });

  it("a chevron keeps focus while the row walks", async () => {
    render(<Harness />);
    steamFocus(down("Telemetry"));
    fireEvent.click(down("Telemetry"));
    await waitFor(() => expect(rowNames()[1]).toBe("Telemetry"));
    expect(document.activeElement).toBe(down("Telemetry"));
  });

  it("when the pressed chevron becomes disabled (row reached the end) focus moves to the other chevron, not into limbo", async () => {
    render(<Harness />);
    steamFocus(up("Now Playing"));
    fireEvent.click(up("Now Playing")); // now first: up is disabled
    await waitFor(() => expect(rowNames()[0]).toBe("Now Playing"));
    expect(up("Now Playing")).toBeDisabled();
    expect(document.activeElement).toBe(down("Now Playing"));

    steamFocus(down("Now Playing"));
    fireEvent.click(down("Now Playing")); // and back down: focus stays on its down chevron
    await waitFor(() => expect(rowNames()[1]).toBe("Now Playing"));
    expect(document.activeElement).toBe(down("Now Playing"));
  });

  it("focus held outside the list is left alone", async () => {
    render(<><button>outside</button><Harness /></>);
    const outside = screen.getByRole("button", { name: "outside" });
    outside.focus();
    fireEvent.click(down("Telemetry"));
    await waitFor(() => expect(rowNames()[1]).toBe("Telemetry"));
    expect(document.activeElement).toBe(outside);
  });

  it("scrolls the focused control into view after the move", async () => {
    const scroll = vi.fn();
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", { value: scroll, configurable: true, writable: true });
    render(<Harness />);
    steamFocus(settings("Telemetry"));
    pad(settings("Telemetry"), "PageDown");
    await waitFor(() => expect(rowNames()[1]).toBe("Telemetry"));
    expect(scroll).toHaveBeenCalledWith({ block: "nearest" });
    delete (HTMLElement.prototype as { scrollIntoView?: unknown }).scrollIntoView;
  });
});

describe("D-pad structure (device: rows were focus stops that swallowed left / right; disabled chevrons were focusable)", () => {
  it("a row is a container, not a focus stop; its controls are the stops", () => {
    render(<Harness />);
    expect(screen.getByRole("group", { name: "Telemetry" })).toHaveAttribute("data-focusable", "false");
    expect(settings("Telemetry")).not.toHaveAttribute("data-focusable", "false");
  });

  it("disabled chevrons and a Settings button without a sheet are not focusable and say so", () => {
    render(<Harness />);
    expect(up("Telemetry")).toBeDisabled();
    expect(up("Telemetry")).toHaveAttribute("aria-disabled", "true");
    expect(up("Telemetry")).toHaveAttribute("data-focusable", "false");
    expect(up("Now Playing")).toHaveAttribute("data-focusable", "true");
    expect(down("Weather")).toHaveAttribute("data-focusable", "false");
    expect(settings("Weather")).toBeDisabled();
    expect(settings("Weather")).toHaveAttribute("data-focusable", "false");
    expect(settings("Telemetry")).toHaveAttribute("data-focusable", "true");
  });

  it("A on a disabled chevron does nothing (on the device it bubbled to the row and opened Settings)", () => {
    render(<Harness />);
    pad(up("Telemetry"), "Enter");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("A on the row itself does nothing: A only activates the focused control (no A on the row, only on its Settings button)", () => {
    render(<Harness />);
    pad(screen.getByRole("group", { name: "Telemetry" }), "Enter");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("L1 on the first row and R1 on the last do nothing", () => {
    render(<Harness />);
    pad(settings("Telemetry"), "PageUp");
    pad(down("Weather").parentElement!, "PageDown");
    expect(rowNames()).toEqual(NAMES);
  });
});

describe("one title: Steam's SidebarNavigation draws the page title", () => {
  it("Live Screens has no h1 and no heading that repeats the page name", async () => {
    backend({ screens: [info("telemetry", { name: "Telemetry" })] });
    render(<ScreensPage />);
    await screen.findByRole("group", { name: "Telemetry" });
    expect(screen.queryByRole("heading", { level: 1 })).toBeNull();
    expect(screen.queryByRole("heading", { name: /^Live Screens$/ })).toBeNull();
  });

  it("Standby Screens has no h1 and the list heading is 'In rotation', not the page name again", async () => {
    backend({ screens: [info("weather", { has_idle: true, name: "Weather", idle_modes: ["mono"], idle_mode: "mono" })], settings: { idle_screens: ["weather"] } });
    render(<IdlePage />);
    await screen.findByRole("group", { name: "Weather" });
    const headings = screen.getAllByRole("heading").map((h) => h.textContent);
    expect(screen.queryByRole("heading", { level: 1 })).toBeNull();
    expect(headings).not.toContain("Standby Screens");
    expect(headings).toContain("In rotation");
  });

  it("Store has no h1", async () => {
    backend({});
    render(<StorePage />);
    await waitFor(() => expect(screen.getByRole("tablist")).toBeInTheDocument());
    expect(screen.queryByRole("heading", { level: 1 })).toBeNull();
  });
});

describe("Standby: every row has the same buttons", () => {
  it("a layout with one render mode shows a disabled Settings button, not a gap", async () => {
    backend({
      screens: [
        info("a", { has_idle: true, name: "A", idle_modes: ["mono", "gray4"], idle_mode: "mono" }),
        info("b", { has_idle: true, name: "B", idle_modes: ["gray4"], idle_mode: "gray4" }),
      ],
      settings: { idle_screens: ["a", "b"] },
    });
    render(<IdlePage />);
    const b = await screen.findByRole("group", { name: "B" });
    expect(within(b).getByRole("button", { name: /^Settings for B/ })).toBeDisabled();
    expect(within(screen.getByRole("group", { name: "A" })).getByRole("button", { name: /^Settings for A/ })).toBeEnabled();
  });
});

describe("stylesheet rules that the device needs (read from Steam's real CSS cascade)", () => {
  const rule = (sel: string) => CSS.split("\n").filter((l) => l.includes(sel)).join("\n");

  it("the picture is border-box with a 1 px border (no bezel padding that widened it past its box)", () => {
    expect(rule(".ink-pic{")).toContain("box-sizing:border-box");
    expect(rule(".ink-pic{")).toContain("border:1px solid");
  });

  it("the status card art is as wide as the picture, and the text column cannot be pushed under it", () => {
    expect(rule(".ink-stat-art{")).toContain("width:156px");
    expect(rule(".ink-stat-main{")).toContain("align-items:flex-start");
    expect(rule(".ink-stat-main>div:not(.ink-stat-art)")).toContain("min-width:0");
  });

  it("buttons force display:inline-flex (Steam's DialogButton sets display:block, which dropped the icon gap)", () => {
    expect(CSS).toMatch(/\.ink-btn\{[^}]*display:inline-flex !important[^}]*gap:8px/);
  });

  it("rows do not wrap on a wide page (a wrapping flex row is a 2-D grid to Steam: left / right died) but wrap when narrow", () => {
    expect(rule(".ink-row{position")).toContain("flex-wrap:nowrap");
    expect(CSS).toMatch(/@media \(max-width:760px\)\{\.ink-row\{flex-wrap:wrap\}/);
  });

  it("the focused row and the focused buttons are highlighted from Steam's own focus classes, not only :focus-within", () => {
    expect(rule(".ink-row:focus-within")).toContain(".ink-row.gpfocuswithin");
    expect(rule(".ink-btn.gpfocus")).toContain("outline:3px solid #fff");
  });
});
