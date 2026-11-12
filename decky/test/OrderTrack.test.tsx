// The order track, L1 / R1, Reorder mode (X), the "..." menu and the focus / motion rules of OrderedList.
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { OrderedList, Slots } from "../src/components/ScreenList";
import { screen as info } from "./fixtures";

const NAMES = ["Telemetry", "Now Playing", "Thermals", "Weather"];
const items = NAMES.map((n) => info(n.toLowerCase().replace(" ", "-"), { name: n, description: `${n} desc` }));
const slots: Slots = { thumb: (s) => <div>pic {s.name}</div>, sheet: (s) => <div>settings body of {s.name}</div> };

const rowNames = () => screen.getAllByRole("group").filter((g) => g.hasAttribute("data-row-id")).map((g) => g.getAttribute("aria-label"));
const row = (name: string) => screen.getByRole("group", { name });
const pad = (el: Element, key: string) => fireEvent.keyDown(el, { key });
const down = (name: string) => screen.getByRole("button", { name: new RegExp(`^Move ${name} down to position`) });
const up = (name: string) => screen.getByRole("button", { name: new RegExp(`^Move ${name} up to position`) });

/** The page's side of the contract: it owns the order and feeds it back. `result` is what the (fake) backend answers. */
function Harness({ onChange, result = { ok: true }, start = items, disabled = false }: {
  onChange: (ids: string[]) => void;
  result?: { ok: boolean };
  start?: typeof items;
  disabled?: boolean;
}) {
  const [list, setList] = useState(start);
  return (
    <OrderedList
      items={list}
      slots={slots}
      disabled={disabled}
      empty="nothing"
      removeLabel="Remove from rotation"
      onChange={async (ids) => {
        onChange(ids);
        if (result.ok) setList(ids.map((id) => start.find((x) => x.id === id)!));
        return result;
      }}
    />
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("order track", () => {
  it("shows a numbered track on every row: position, up, down", () => {
    render(<Harness onChange={() => undefined} />);
    expect(rowNames()).toEqual(NAMES);
    NAMES.forEach((n, i) => {
      const track = within(row(n)).getByRole("group", { name: `Position ${i + 1} of 4` });
      expect(track).toHaveTextContent(String(i + 1));
      expect(within(track).getAllByRole("button")).toHaveLength(2);
    });
  });

  it("disables up on the first row and down on the last; the middle ones have both", () => {
    render(<Harness onChange={() => undefined} />);
    expect(up("Telemetry")).toBeDisabled();
    expect(down("Telemetry")).toBeEnabled();
    expect(up("Weather")).toBeEnabled();
    expect(down("Weather")).toBeDisabled();
    expect(up("Thermals")).toBeEnabled();
    expect(down("Thermals")).toBeEnabled();
  });

  it("a single row has both chevrons disabled", () => {
    render(<Harness onChange={() => undefined} start={items.slice(0, 1)} />);
    expect(up("Telemetry")).toBeDisabled();
    expect(down("Telemetry")).toBeDisabled();
  });

  it("the chevron names say where the row will go", () => {
    render(<Harness onChange={() => undefined} />);
    expect(down("Now Playing")).toHaveAccessibleName("Move Now Playing down to position 3");
    expect(up("Now Playing")).toHaveAccessibleName("Move Now Playing up to position 1");
  });

  it("the track hit areas are 44 x 40 px (stylesheet), and the buttons are plain DialogButtons, not a Steam Field", async () => {
    const { CSS } = await import("../src/components/styles");
    expect(CSS).toMatch(/\.ink-ord-btn\{[^}]*width:44px !important;height:40px !important/);
    expect(CSS).toMatch(/\.ink-ord\{[^}]*width:44px/);
    render(<Harness onChange={() => undefined} />);
    expect(down("Telemetry").className).toContain("ink-ord-btn");
    expect(down("Telemetry").closest('[role="group"][aria-label^="Position"]')).not.toBeNull();
  });

  it("clicking a chevron moves the row at once, reports the full new order, and says where it went", async () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    fireEvent.click(down("Telemetry"));
    expect(onChange).toHaveBeenCalledWith(["now-playing", "telemetry", "thermals", "weather"]);
    expect(rowNames()).toEqual(["Now Playing", "Telemetry", "Thermals", "Weather"]);
    expect(screen.getByRole("status")).toHaveTextContent("Telemetry is now 2 of 4");
    fireEvent.click(up("Weather"));
    expect(onChange).toHaveBeenLastCalledWith(["now-playing", "telemetry", "weather", "thermals"]);
    expect(screen.getByRole("status")).toHaveTextContent("Weather is now 3 of 4");
  });

  it("numbers follow the rows after a move", () => {
    render(<Harness onChange={() => undefined} />);
    fireEvent.click(down("Telemetry"));
    expect(within(row("Telemetry")).getByRole("group", { name: "Position 2 of 4" })).toBeInTheDocument();
    expect(within(row("Now Playing")).getByRole("group", { name: "Position 1 of 4" })).toBeInTheDocument();
    expect(up("Now Playing")).toBeDisabled();
  });

  it("rapid moves chain on the order the user just chose, not on a stale one", () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} result={{ ok: true }} />);
    // second click happens before the parent has fed the first result back
    const b = down("Telemetry");
    fireEvent.click(b);
    fireEvent.click(b);
    expect(onChange).toHaveBeenNthCalledWith(1, ["now-playing", "telemetry", "thermals", "weather"]);
    expect(onChange).toHaveBeenNthCalledWith(2, ["now-playing", "thermals", "telemetry", "weather"]);
  });

  it("rolls the row back when the backend refuses", async () => {
    render(<Harness onChange={() => undefined} result={{ ok: false }} />);
    fireEvent.click(down("Telemetry"));
    await waitFor(() => expect(rowNames()).toEqual(NAMES));
  });

  it("the buttons are not locked while a request is in flight (a disabled button would lose focus)", () => {
    render(<OrderedList items={items} slots={slots} empty="x" removeLabel="Remove" onChange={() => new Promise(() => undefined)} />);
    fireEvent.click(down("Telemetry"));
    expect(down("Telemetry")).toBeEnabled();
  });

  it("a locked list (disabled) cannot be moved by chevron, bumper, menu or X", () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} disabled />);
    expect(down("Telemetry")).toBeDisabled();
    expect(up("Weather")).toBeDisabled();
    pad(row("Now Playing"), "PageDown");
    pad(row("Now Playing"), "x");
    pad(row("Now Playing"), "y");
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.queryByRole("menu")).toBeNull();
    expect(screen.getByRole("button", { name: "More for Now Playing" })).toBeDisabled();
  });
});

describe("focus stays on the moved row", () => {
  it("the chevron you pressed keeps focus through several moves", () => {
    render(<Harness onChange={() => undefined} />);
    const b = down("Telemetry");
    b.focus();
    fireEvent.click(b);
    expect(document.activeElement).toBe(b);
    expect(rowNames()[1]).toBe("Telemetry");
    fireEvent.click(b);
    expect(document.activeElement).toBe(b);
    expect(rowNames()[2]).toBe("Telemetry");
  });

  it("the focused row itself keeps focus when L1 / R1 move it", () => {
    render(<Harness onChange={() => undefined} />);
    const r = row("Now Playing");
    r.focus();
    pad(r, "PageDown");
    expect(rowNames()).toEqual(["Telemetry", "Thermals", "Now Playing", "Weather"]);
    expect(document.activeElement).toBe(r);
    pad(r, "PageUp");
    expect(rowNames()).toEqual(NAMES);
    expect(document.activeElement).toBe(r);
  });

  it("focus is put back if the DOM move dropped it", () => {
    render(<Harness onChange={() => undefined} />);
    const r = row("Now Playing");
    r.focus();
    const spy = vi.spyOn(r, "focus");
    pad(r, "PageDown");
    // jsdom keeps focus on a moved node; either way the row is focused afterwards
    expect(document.activeElement).toBe(r);
    spy.mockRestore();
  });
});

describe("L1 / R1", () => {
  it("L1 moves the focused row up, R1 down, by one", () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    pad(row("Thermals"), "PageUp");
    expect(onChange).toHaveBeenLastCalledWith(["telemetry", "thermals", "now-playing", "weather"]);
    pad(row("Telemetry"), "PageDown");
    expect(onChange).toHaveBeenLastCalledWith(["thermals", "telemetry", "now-playing", "weather"]);
  });

  it("works from a button inside the row too (the event bubbles), and does not leak to the row above", () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    pad(screen.getByRole("button", { name: "Settings for Thermals" }), "PageDown");
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenCalledWith(["telemetry", "now-playing", "weather", "thermals"]);
  });

  it("does nothing at the ends", () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    pad(row("Telemetry"), "PageUp");
    pad(row("Weather"), "PageDown");
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("ignores other buttons", () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    for (const key of ["ArrowDown", "ArrowUp", "a", "b"]) pad(row("Thermals"), key);
    expect(onChange).not.toHaveBeenCalled();
  });
});

describe("Reorder mode (X)", () => {
  it("X lifts the row; the hint legend switches to D-pad / A / B", () => {
    render(<Harness onChange={() => undefined} />);
    const r = row("Thermals");
    expect(r).not.toHaveAttribute("data-lift");
    expect(r.querySelector(".ink-hints")).toHaveTextContent("L1Move upR1Move downXReorderYRemoveMenuMore");
    pad(r, "x");
    expect(r).toHaveAttribute("data-lift", "true");
    expect(r.querySelector(".ink-hints")).toHaveTextContent("D-padMoveADoneBCancel");
    expect(r.getAttribute("data-ok")).toBe("Done");
    expect(r.getAttribute("data-cancel")).toBe("Cancel");
  });

  it("without Reorder mode the D-pad is left to Steam (no move) and A on the row opens nothing", () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    pad(row("Thermals"), "ArrowDown");
    expect(onChange).not.toHaveBeenCalled();
    pad(row("Thermals"), "Enter");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("D-pad moves the lifted row; A keeps the place", () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    const r = row("Telemetry");
    r.focus();
    pad(r, "x");
    pad(r, "ArrowDown");
    pad(r, "ArrowDown");
    expect(rowNames()).toEqual(["Now Playing", "Thermals", "Telemetry", "Weather"]);
    expect(onChange).toHaveBeenCalledTimes(2);
    expect(r).toHaveAttribute("data-lift", "true"); // still lifted between presses
    pad(r, "Enter");
    expect(r).not.toHaveAttribute("data-lift");
    expect(rowNames()).toEqual(["Now Playing", "Thermals", "Telemetry", "Weather"]);
    expect(screen.queryByRole("dialog")).toBeNull(); // A put the row down; it did not also open Settings
    expect(document.activeElement).toBe(r);
  });

  it("B puts the row back where it was and tells the backend", async () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    const r = row("Telemetry");
    pad(r, "x");
    pad(r, "ArrowDown");
    pad(r, "ArrowDown");
    onChange.mockClear();
    pad(r, "Escape");
    await waitFor(() => expect(rowNames()).toEqual(NAMES));
    expect(onChange).toHaveBeenCalledWith(["telemetry", "now-playing", "thermals", "weather"]);
    expect(r).not.toHaveAttribute("data-lift");
  });

  it("B on a row that did not move sends nothing", () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    const r = row("Telemetry");
    pad(r, "x");
    pad(r, "Escape");
    expect(onChange).not.toHaveBeenCalled();
    expect(r).not.toHaveAttribute("data-lift");
  });

  it("D-pad up on the first row (and down on the last) stays put", () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    pad(row("Telemetry"), "x");
    pad(row("Telemetry"), "ArrowUp");
    expect(onChange).not.toHaveBeenCalled();
    expect(rowNames()).toEqual(NAMES);
  });

  it("X again also puts the row down", () => {
    render(<Harness onChange={() => undefined} />);
    pad(row("Telemetry"), "x");
    pad(row("Telemetry"), "x");
    expect(row("Telemetry")).not.toHaveAttribute("data-lift");
  });

  it("focus moving to another row puts the lifted row down", () => {
    render(<Harness onChange={() => undefined} />);
    pad(row("Telemetry"), "x");
    expect(row("Telemetry")).toHaveAttribute("data-lift", "true");
    fireEvent.focusIn(row("Weather"));
    expect(row("Telemetry")).not.toHaveAttribute("data-lift");
  });

  it("focus moving between the lifted row's own buttons keeps it lifted", () => {
    render(<Harness onChange={() => undefined} />);
    pad(row("Telemetry"), "x");
    fireEvent.focusIn(down("Telemetry"));
    expect(row("Telemetry")).toHaveAttribute("data-lift", "true");
  });

  it("only one row is lifted at a time", () => {
    render(<Harness onChange={() => undefined} />);
    pad(row("Telemetry"), "x");
    pad(row("Weather"), "x");
    expect(document.querySelectorAll('[data-lift="true"]')).toHaveLength(1);
    expect(row("Weather")).toHaveAttribute("data-lift", "true");
  });
});

describe("hint legend", () => {
  it("dims the hint for a move that cannot happen", () => {
    render(<Harness onChange={() => undefined} />);
    const hints = (n: string) => Array.from(row(n).querySelectorAll<HTMLElement>(".ink-hint")).map((h) => [h.textContent, h.dataset.off === "true"]);
    expect(hints("Telemetry")).toContainEqual(["L1Move up", true]);
    expect(hints("Telemetry")).toContainEqual(["R1Move down", false]);
    expect(hints("Weather")).toContainEqual(["R1Move down", true]);
    expect(hints("Weather")).toContainEqual(["L1Move up", false]);
  });

  it("tells Steam's own footer legend what each button does", () => {
    render(<Harness onChange={() => undefined} />);
    const r = row("Thermals");
    expect(r.hasAttribute("data-ok")).toBe(false); // no description: Steam's default footer says A SELECT
    expect(r.getAttribute("data-secondary")).toBe("Reorder");
    expect(r.getAttribute("data-options")).toBe("Remove");
    expect(r.getAttribute("data-menu")).toBe("More");
  });

  it("is hidden from screen readers (the buttons and the status line carry the meaning)", () => {
    render(<Harness onChange={() => undefined} />);
    expect(row("Thermals").querySelector(".ink-hints")).toHaveAttribute("aria-hidden", "true");
  });

  it("the legend only shows on the focused row (stylesheet rule), inside the text column so the row does not grow", async () => {
    const { CSS } = await import("../src/components/styles");
    expect(CSS).toMatch(/\.ink-hints\{[^}]*display:none/);
    expect(CSS).toMatch(/\.ink-row:focus-within \.ink-hints[^{]*\{display:flex\}/);
    render(<Harness onChange={() => undefined} />);
    expect(row("Thermals").querySelector(".ink-row-body > .ink-hints")).not.toBeNull();
  });
});

describe('the "..." menu', () => {
  const open = (name: string) => fireEvent.click(screen.getByRole("button", { name: `More for ${name}` }));

  it("lists the page's entries, then Move up, Move down, Remove", () => {
    const withPin: Slots = { ...slots, menu: () => [{ label: "Pin to panel", onSelect: () => undefined }] };
    render(<OrderedList items={items} slots={withPin} empty="x" removeLabel="Remove from rotation" onChange={() => undefined} />);
    open("Thermals");
    expect(within(screen.getByRole("menu", { name: "Thermals actions" })).getAllByRole("menuitem").map((m) => m.textContent)).toEqual(
      ["Pin to panel", "Move up", "Move down", "Remove from rotation"],
    );
  });

  it("Move down / Move up from the menu reorder like the chevrons", () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    open("Telemetry");
    fireEvent.click(screen.getByRole("menuitem", { name: "Move down" }));
    expect(onChange).toHaveBeenCalledWith(["now-playing", "telemetry", "thermals", "weather"]);
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("disables Move up on the first row and Move down on the last", () => {
    render(<Harness onChange={() => undefined} />);
    open("Telemetry");
    expect(screen.getByRole("menuitem", { name: "Move up" })).toBeDisabled();
    expect(screen.getByRole("menuitem", { name: "Move down" })).toBeEnabled();
    fireEvent.click(screen.getByRole("menuitem", { name: "Move down" }));
    open("Weather");
    expect(screen.getByRole("menuitem", { name: "Move down" })).toBeDisabled();
  });

  it("Remove sends the order without that screen", () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    open("Now Playing");
    fireEvent.click(screen.getByRole("menuitem", { name: "Remove from rotation" }));
    expect(onChange).toHaveBeenCalledWith(["telemetry", "thermals", "weather"]);
  });

  it("the Menu button opens the menu from the gamepad (Y removes instead)", () => {
    render(<Harness onChange={() => undefined} />);
    pad(row("Thermals"), "m");
    expect(screen.getByRole("menu", { name: "Thermals actions" })).toBeInTheDocument();
  });

  it("the menu acts on the order at the time of the choice, not the one when it opened", () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    open("Telemetry"); // opened on the original order
    fireEvent.click(down("Now Playing")); // the order changes while the menu is open
    onChange.mockClear();
    fireEvent.click(screen.getByRole("menuitem", { name: "Move down" }));
    expect(onChange).toHaveBeenCalledWith(["thermals", "telemetry", "now-playing", "weather"]);
  });
});

describe("Settings button and sheet", () => {
  it("opens the sheet for that screen, with its title, meta and picture, and no Done (B closes it)", () => {
    render(<Harness onChange={() => undefined} />);
    fireEvent.click(screen.getByRole("button", { name: "Settings for Thermals" }));
    const dlg = screen.getByRole("dialog", { name: "Thermals" });
    expect(dlg).toHaveTextContent("pic Thermals");
    expect(dlg).toHaveTextContent("settings body of Thermals");
    expect(dlg).toHaveTextContent("Built-in · v1.0.0");
    expect(within(dlg).queryByRole("button", { name: "Done" })).toBeNull();
    fireEvent.keyDown(dlg, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("Escape (B) closes the sheet", () => {
    render(<Harness onChange={() => undefined} />);
    fireEvent.click(screen.getByRole("button", { name: "Settings for Thermals" }));
    fireEvent.keyDown(screen.getByRole("dialog", { name: "Thermals" }), { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("a screen with nothing to configure has a disabled Settings button (same layout in every row) and no A hint", () => {
    render(<OrderedList items={items} slots={{ thumb: slots.thumb }} empty="x" removeLabel="r" onChange={() => undefined} />);
    for (const b of screen.getAllByRole("button", { name: /^Settings/ })) expect(b).toBeDisabled();
    expect(row("Thermals").querySelector(".ink-hints")).not.toHaveTextContent("Settings");
    pad(row("Thermals"), "Enter");
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

describe("motion", () => {
  const animate = vi.fn(() => ({ onfinish: null, oncancel: null }));
  const stub = (reduced: boolean) => {
    vi.stubGlobal("matchMedia", (q: string) => ({ matches: reduced && q.includes("reduce"), media: q, addEventListener: () => undefined, removeEventListener: () => undefined }));
    Object.defineProperty(HTMLElement.prototype, "animate", { value: animate, configurable: true, writable: true });
    animate.mockClear();
  };
  afterEach(() => { delete (HTMLElement.prototype as { animate?: unknown }).animate; });

  it("rows slide for 220 ms with the ease-out curve when the order changes (when they actually moved)", () => {
    stub(false);
    // jsdom has no layout: give each row a position from its index in the DOM so the swap shows a delta
    const rect = vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
      const rows = Array.from(document.querySelectorAll("[data-row-id]"));
      const i = rows.indexOf(this);
      return { top: i < 0 ? 0 : i * 100, bottom: 0, left: 0, right: 0, width: 0, height: 0, x: 0, y: 0, toJSON: () => ({}) } as DOMRect;
    });
    render(<Harness onChange={() => undefined} />);
    act(() => { fireEvent.click(down("Telemetry")); });
    expect(animate).toHaveBeenCalled();
    const [frames, opts] = animate.mock.calls[0] as unknown as [{ transform: string }[], { duration: number; easing: string }];
    expect(opts.duration).toBe(220);
    expect(opts.easing).toBe("cubic-bezier(.16,1,.3,1)");
    expect(frames[1].transform).toBe("none");
    expect(animate).toHaveBeenCalledTimes(2); // the two swapped rows only
    rect.mockRestore();
  });

  it("prefers-reduced-motion: the rows swap without any animation, focus and the status line still update", () => {
    stub(true);
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
      const i = Array.from(document.querySelectorAll("[data-row-id]")).indexOf(this);
      return { top: i * 100 } as DOMRect;
    });
    render(<Harness onChange={() => undefined} />);
    const b = down("Telemetry");
    b.focus();
    fireEvent.click(b);
    expect(animate).not.toHaveBeenCalled();
    expect(rowNames()[1]).toBe("Telemetry");
    expect(document.activeElement).toBe(b);
    expect(screen.getByRole("status")).toHaveTextContent("Telemetry is now 2 of 4");
  });

  it("the stylesheet also drops every transition under reduced motion", async () => {
    const { CSS } = await import("../src/components/styles");
    expect(CSS).toMatch(/@media \(prefers-reduced-motion:reduce\)\{[^@]*\.ink-row[^}]*transition:none/);
  });
});

describe("empty and unknown", () => {
  it("shows the empty note instead of a list", () => {
    render(<OrderedList items={[]} slots={slots} empty="Nothing in the rotation." removeLabel="r" onChange={() => undefined} />);
    expect(screen.getByText("Nothing in the rotation.")).toBeInTheDocument();
    expect(screen.queryByRole("status")).toBeNull();
  });
});
