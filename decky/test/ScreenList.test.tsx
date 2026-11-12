import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { readFileSync, readdirSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it, vi } from "vitest";
import { AvailableList, OrderedList, Slots, StatusCard, moved, originLine } from "../src/components/ScreenList";
import { CSS } from "../src/components/ui";
import { IdlePage } from "../src/pages/IdlePage";
import { ScreensPage } from "../src/pages/ScreensPage";
import { backend, screen as scr, status } from "./fixtures";

const slots = (o: Partial<Slots> = {}): Slots => ({ thumb: (s) => <div data-testid={`thumb-${s.id}`} />, ...o });
const items = [scr("a", { name: "Alpha" }), scr("b", { name: "Beta" }), scr("c", { name: "Gamma" })];

/** Opens a row's "..." menu and returns the menu, so a test can click a menuitem. */
function openMenu(rowName: string) {
  fireEvent.click(within(screen.getByRole("group", { name: rowName })).getByRole("button", { name: `More for ${rowName}` }));
  return within(screen.getByRole("menu"));
}

describe("moved", () => {
  it("swaps with the neighbour", () => {
    expect(moved(["a", "b", "c"], 1, -1)).toEqual(["b", "a", "c"]);
    expect(moved(["a", "b", "c"], 1, 1)).toEqual(["a", "c", "b"]);
  });
  it("leaves the list alone at either end, and does not change its input", () => {
    const l = ["a", "b"];
    expect(moved(l, 0, -1)).toBe(l);
    expect(moved(l, 1, 1)).toBe(l);
    expect(moved(l, 0, 1)).toEqual(["b", "a"]);
    expect(l).toEqual(["a", "b"]);
  });
  it("copes with an empty list and a single item", () => {
    expect(moved([], 0, 1)).toEqual([]);
    expect(moved(["a"], 0, -1)).toEqual(["a"]);
  });
});

describe("originLine", () => {
  it("built-in, third party, code", () => {
    expect(originLine(scr("a"))).toBe("Built-in · v1.0.0");
    expect(originLine(scr("a", { builtin: false, author: "Ada", has_code: true }))).toBe("by Ada · v1.0.0 · runs code");
  });
});

describe("StatusCard", () => {
  it("is a labelled section with the title, the sentence, the facts and whatever children it is given", () => {
    render(
      <StatusCard label="Panel" tone="ok" title="Panel connected" sub="Showing Clock" art={<div data-testid="art" />}
        facts={[{ label: "Firmware", value: "1.2.3" }, { label: "Wi-Fi", value: "HomeNet" }]}>
        <button>Open settings</button>
      </StatusCard>,
    );
    const c = within(screen.getByRole("region", { name: "Panel" }));
    expect(c.getByRole("heading", { level: 2 })).toHaveTextContent("Panel connected");
    expect(c.getByText("Showing Clock")).toBeInTheDocument();
    expect(c.getByTestId("art")).toBeInTheDocument();
    expect(c.getByRole("button", { name: "Open settings" })).toBeInTheDocument();
    // facts are name -> value pairs
    for (const [dt, dd] of [["Firmware", "1.2.3"], ["Wi-Fi", "HomeNet"]]) {
      expect(c.getByText(dt).tagName).toBe("DT");
      expect(c.getByText(dd).tagName).toBe("DD");
    }
  });
  it("drops the picture, the sentence and the facts when there is nothing to show", () => {
    render(<StatusCard label="Overview" title="Only a title" />);
    const section = screen.getByRole("region", { name: "Overview" });
    expect(within(section).getByRole("heading", { level: 2 })).toHaveTextContent("Only a title");
    expect(section.querySelector(".ink-stat-art")).toBeNull();
    expect(section.querySelector(".ink-facts")).toBeNull();
  });
  it("the title has no decorative dot unless a tone is given", () => {
    const { rerender, container } = render(<StatusCard label="Overview" title="No dot" />);
    expect(container.querySelector(".ink-dot")).toBeNull();
    rerender(<StatusCard label="Overview" tone="error" title="With dot" />);
    const dot = container.querySelector(".ink-dot");
    expect(dot).toHaveClass("ink-dot-error");
    expect(dot).toHaveAttribute("aria-hidden", "true"); // the title carries the meaning, not the colour
  });
});

describe("OrderedList", () => {
  const props = { items, slots: slots(), empty: "Nothing here.", removeLabel: "Remove", onChange: vi.fn() };

  it("is a group per screen, named and numbered, with an order track that says N of M", () => {
    render(<OrderedList {...props} />);
    expect(screen.getAllByRole("group", { name: /Alpha|Beta|Gamma/ }).map((g) => g.getAttribute("aria-label"))).toEqual(["Alpha", "Beta", "Gamma"]);
    expect(screen.getByRole("group", { name: "Position 1 of 3" })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Position 3 of 3" })).toBeInTheDocument();
    for (const [id, i] of [["a", 1], ["b", 2], ["c", 3]] as const) {
      expect(screen.getByRole("group", { name: items[i - 1].name }).getAttribute("data-row-id")).toBe(id);
    }
  });

  it("the chevrons move a row and report the whole new id order", () => {
    const onChange = vi.fn();
    render(<OrderedList {...props} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: "Move Gamma up to position 2" }));
    expect(onChange).toHaveBeenLastCalledWith(["a", "c", "b"]);
    // optimistic: the order changed in place, so Gamma is now second
    fireEvent.click(screen.getByRole("button", { name: "Move Alpha down to position 2" }));
    expect(onChange).toHaveBeenLastCalledWith(["c", "a", "b"]);
    expect(onChange).toHaveBeenCalledTimes(2);
  });

  it("announces the new position in a status line after a move", () => {
    render(<OrderedList {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "Move Beta up to position 1" }));
    expect(screen.getByRole("status")).toHaveTextContent("Beta is now 1 of 3");
  });

  it("rolls the order back when the backend refuses the move", async () => {
    const onChange = vi.fn(() => ({ ok: false }));
    render(<OrderedList {...props} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: "Move Beta up to position 1" }));
    expect(onChange).toHaveBeenCalledWith(["b", "a", "c"]);
    // the rejected call restores the original order, so Beta is second again
    await waitFor(() =>
      expect(screen.getAllByRole("group", { name: /Alpha|Beta|Gamma/ }).map((g) => g.getAttribute("aria-label"))).toEqual(["Alpha", "Beta", "Gamma"]));
  });

  it("first cannot go up, last cannot go down", () => {
    render(<OrderedList {...props} />);
    expect(screen.getByRole("button", { name: "Move Alpha up to position 0" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Move Gamma down to position 4" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Move Beta up to position 1" })).not.toBeDisabled();
    expect(screen.getByRole("button", { name: "Move Beta down to position 3" })).not.toBeDisabled();
  });

  it("the '...' menu moves and removes, naming the screen; the remove label is the page's own", () => {
    const onChange = vi.fn();
    render(<OrderedList {...props} removeLabel="Remove from rotation" onChange={onChange} />);
    let m = openMenu("Beta");
    m.getByRole("menuitem", { name: "Move up" });
    fireEvent.click(m.getByRole("menuitem", { name: "Move down" }));
    expect(onChange).toHaveBeenLastCalledWith(["a", "c", "b"]);
    m = openMenu("Beta");
    fireEvent.click(m.getByRole("menuitem", { name: "Remove from rotation" }));
    expect(onChange).toHaveBeenLastCalledWith(["a", "c"]);
  });

  it("the menu's Move up is disabled on the first row, Move down on the last", () => {
    render(<OrderedList {...props} />);
    expect(openMenu("Alpha").getByRole("menuitem", { name: "Move up" })).toBeDisabled();
    expect(openMenu("Gamma").getByRole("menuitem", { name: "Move down" })).toBeDisabled();
  });

  it("page menu entries come before Move up / Move down / Remove", () => {
    render(<OrderedList {...props} removeLabel="Remove" slots={slots({ menu: (s) => [{ label: `Pin ${s.name}`, onSelect: () => undefined }] })} />);
    expect(openMenu("Alpha").getAllByRole("menuitem").map((b) => b.textContent)).toEqual(["Pin Alpha", "Move up", "Move down", "Remove"]);
  });

  it("the More button advertises a menu for assistive tech", () => {
    render(<OrderedList {...props} />);
    expect(screen.getByRole("button", { name: "More for Alpha" })).toHaveAttribute("aria-haspopup", "menu");
  });

  it("a Settings button appears only when the screen has a sheet, and opens a modal dialog with the sheet body", () => {
    render(<OrderedList {...props} slots={slots({ sheet: (s) => (s.id === "a" ? <p>inside {s.name}</p> : null) })} />);
    const alpha = within(screen.getByRole("group", { name: "Alpha" }));
    const beta = within(screen.getByRole("group", { name: "Beta" }));
    expect(beta.getByRole("button", { name: /Settings/ })).toBeDisabled(); // same buttons in every row; nothing to configure = disabled
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.click(alpha.getByRole("button", { name: "Settings for Alpha" }));
    const sheet = within(screen.getByRole("dialog", { name: "Alpha" }));
    expect(sheet.getByText("inside Alpha")).toBeInTheDocument();
    expect(sheet.queryByRole("button", { name: "Done" })).toBeNull();
    fireEvent.keyDown(screen.getByRole("dialog", { name: "Alpha" }), { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("disabled locks the chevrons, the More button and the menu items, but not Settings (focus must survive)", () => {
    render(<OrderedList {...props} disabled slots={slots({ sheet: () => <p /> })} />);
    expect(screen.getByRole("button", { name: "Move Beta up to position 1" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Move Beta down to position 3" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "More for Beta" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Settings for Beta" })).not.toBeDisabled();
  });

  it("empty shows the hint instead of rows", () => {
    render(<OrderedList {...props} items={[]} />);
    expect(screen.getByText("Nothing here.")).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: /Alpha|Beta|Gamma/ })).toBeNull();
  });

  it("dims a screen the slot says is off", () => {
    render(<OrderedList {...props} slots={slots({ off: (s) => s.id === "a" })} />);
    expect(screen.getByRole("group", { name: "Alpha" })).toHaveAttribute("data-off", "true");
    expect(screen.getByRole("group", { name: "Beta" })).not.toHaveAttribute("data-off");
  });

  it("warns about approval and shows a screen's error", () => {
    render(<OrderedList {...props} items={[scr("a", { name: "Alpha", needs_approval: true, error: "bad file" })]} />);
    expect(screen.getByText(/Waiting for your approval/)).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("bad file");
  });
});

describe("AvailableList", () => {
  it("each row has one Add naming the screen; a blocked one is disabled and not in the order", () => {
    const onAdd = vi.fn();
    render(<AvailableList items={items} slots={slots({ addBlocked: (s) => (s.id === "b" ? "no" : null) })} onAdd={onAdd} />);
    fireEvent.click(screen.getByRole("button", { name: "Add Alpha" }));
    expect(onAdd).toHaveBeenCalledWith("a");
    expect(screen.getByRole("button", { name: "Add Beta" })).toBeDisabled();
    expect(screen.queryByRole("group", { name: /Position/ })).toBeNull(); // not in the order yet
    expect(screen.queryByRole("button", { name: /Remove|Move|More|Settings/ })).toBeNull();
  });
  it("disabled locks Add", () => {
    render(<AvailableList items={items} slots={slots()} disabled onAdd={() => undefined} />);
    expect(screen.getByRole("button", { name: "Add Alpha" })).toBeDisabled();
  });
  it("nothing to show: the hint if given, else nothing", () => {
    const { container, rerender } = render(<AvailableList items={[]} slots={slots()} onAdd={() => undefined} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<AvailableList items={[]} slots={slots()} empty="All added." onAdd={() => undefined} />);
    expect(screen.getByText("All added.")).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------------------------------------------
// The overflow bug: in Gaming Mode the up/down arrows and the action buttons ran off the edge of the page. They were
// children of a Steam `Field` (childrenLayout="inline"); Steam gives a Field's right column `flex-shrink: 0` and, in a
// page (not the quick-access menu), its inner box `min-width: 250px`, so on a narrow page the row cannot fit and spills
// out, and focusing a button slid the page off the left edge. These tests pin what keeps the new layout from doing that.
// ---------------------------------------------------------------------------------------------------------------
/** The declarations of the first rule whose selector is exactly `sel` (whitespace-insensitive), as a Map. */
function rule(sel: string): Map<string, string> {
  // A selector starts a rule after a `}`, a `*/` (a leading comment) or the start of the stylesheet.
  const m = new RegExp(`(?:^|\\}|\\*/)\\s*${sel.replace(/[.[\]]/g, "\\$&")}\\s*\\{([^}]*)\\}`).exec(CSS);
  expect(m, `no rule for ${sel}`).not.toBeNull();
  return new Map(m![1].split(";").map((d) => d.trim()).filter(Boolean).map((d) => { const i = d.indexOf(":"); return [d.slice(0, i).trim(), d.slice(i + 1).trim()]; }));
}

describe("layout: a row and its buttons cannot run off the page", () => {
  it("the action bar wraps, may shrink to nothing, never exceeds its row and starts at the left", () => {
    const bar = rule(".ink-row-act");
    expect(bar.get("display")).toBe("flex");
    expect(bar.get("flex-wrap")).toBe("wrap");
    expect(bar.get("min-width")).toBe("0");
    expect(bar.get("max-width")).toBe("100%");
    expect(bar.get("justify-content")).toBe("flex-start");
  });
  it("a button never forces the row wider: no minimum width, may shrink, nowrap text, own positioning context", () => {
    const btn = rule(".ink-btn");
    expect(btn.get("min-width")).toBe("0 !important"); // Steam's .DialogButton asks for 160 px
    expect(btn.get("flex")).toBe("0 1 auto !important");
    expect(btn.get("width")).toBe("auto !important");
    expect(btn.get("max-width")).toBe("100%");
    expect(btn.get("position")).toBe("relative"); // the screen-reader text is absolutely positioned: keep it inside the button
  });
  it("the row itself can shrink, never exceeds the page, and wraps only on a narrow page (nowrap = Steam's horizontal D-pad layout)", () => {
    const row = rule(".ink-row");
    expect(row.get("min-width")).toBe("0");
    expect(row.get("max-width")).toBe("100%");
    expect(row.get("box-sizing")).toBe("border-box");
    expect(row.get("flex-wrap")).toBe("nowrap"); // wide page: a wrapping flex row is read as a 2-D grid by Steam and left / right stop working
    expect(CSS).toMatch(/@media \(max-width:760px\)\{\.ink-row\{flex-wrap:wrap\}/); // narrow page: the picture and text stack instead of squeezing the bar
    expect(rule(".ink-row-body").get("min-width")).toBe("0");
  });
  it("the order track is a fixed-width column of its own, not part of the wrapping bar", () => {
    const ord = rule(".ink-ord");
    expect(ord.get("flex")).toBe("none");
    expect(ord.get("flex-direction")).toBe("column");
  });
  it("nothing in the stylesheet can push content out of its box", () => {
    expect(CSS).not.toMatch(/justify-content:\s*(flex-end|end|right)/); // overflow of a flex-end row goes off the start edge
    expect(CSS).not.toMatch(/margin[a-z-]*:[^;}]*-\d/);                 // no negative margin anywhere
    // No `margin: auto` on the row/bar/button chain: auto side-margins there would push the controls off the edge.
    // (The Store's empty-state panel centres itself with `margin:18px auto`, which is fine and not on this chain.)
    // The wrapping chain (row, action bar, buttons, status card) may not fix a width or use auto side-margins.
    // The order track (.ink-ord / .ink-ord-btn) is a separate FIXED 44 px touch-target column by design, so it is excluded.
    for (const sel of [".ink-row", ".ink-row-act", ".ink-bar", ".ink-btn", ".ink-stat"]) {
      const r = rule(sel);
      expect(Object.fromEntries(r), `${sel} uses margin:auto`).not.toMatchObject({ margin: expect.stringContaining("auto") });
      const w = r.get("width");
      expect(w === undefined || !/^\d+px/.test(w), `${sel} has a fixed width`).toBe(true);
    }
  });
  it("the list components and pages do not use a Steam Field for anything with buttons", () => {
    const src = (p: string) => readFileSync(resolve("src", p), "utf8"); // vitest runs from decky/
    for (const f of ["components/ScreenList.tsx", "pages/ScreensPage.tsx", "pages/IdlePage.tsx"]) {
      const code = src(f);
      expect(code, f).not.toMatch(/import\s*\{[^}]*\bField\b[^}]*\}\s*from\s*"@decky\/ui"/);
      expect(code, f).not.toMatch(/childrenLayout|childrenContainerWidth/);
    }
    // the old split-out rotation component is gone; one component set serves both pages
    expect(readdirSync(resolve("src/components")).sort()).not.toContain("RotationList.tsx");
  });
  it("in the DOM an 'in' row holds its order track plus a wrapping action bar, each button class-tagged and not shifted", () => {
    render(<OrderedList {...{ items, slots: slots({ sheet: () => <p /> }), empty: "", removeLabel: "Remove", onChange: () => undefined }} />);
    for (const row of screen.getAllByRole("group", { name: /Alpha|Beta|Gamma/ })) {
      expect(row.querySelector(".ink-ord")).not.toBeNull(); // the order track
      const bar = row.querySelector(".ink-row-act") as HTMLElement;
      expect(bar.parentElement).toBe(row);
      const buttons = within(bar).getAllByRole("button");
      expect(buttons.length).toBeGreaterThanOrEqual(2); // Settings + More
      for (const b of buttons) {
        expect(b.parentElement).toBe(bar);
        expect(b).toHaveClass("ink-btn");
        expect(b.style.width + b.style.minWidth + b.style.marginLeft + b.style.left).toBe("");
      }
    }
  });
});

describe("one component set behind both pages", () => {
  /** Tag + class skeleton of a row down to its parts: what the page-specific slots cannot change. */
  const skeleton = (row: Element): string => {
    const kids = [...row.children].map((c) => c.className.split(" ").filter((x) => /^ink-(ord|row-art|row-body|row-act)$/.test(x)).join(".")).join(",");
    return `${row.tagName}.${row.className}[${kids}]|${row.getAttribute("role")}`;
  };
  it("a screen in the Live rotation and one in the Standby selection are the same row", async () => {
    backend({
      screens: [scr("clock", { name: "Clock" }), scr("idler", { name: "Idler", has_idle: true, idle_modes: ["mono", "gray4"] }), scr("idler2", { name: "Idler 2", has_idle: true, has_active: false })],
      settings: { rotation: ["clock"], idle_screens: ["idler", "idler2"] },
      status: { idle_supported: true },
    });
    const a = render(<ScreensPage />);
    const s = skeleton(await a.findByRole("group", { name: "Clock" }));
    a.unmount();
    const b = render(<IdlePage />);
    expect(skeleton(await b.findByRole("group", { name: "Idler" }))).toBe(s);
    expect(skeleton(await b.findByRole("group", { name: "Idler 2" }))).toBe(s);
  });
  it("the same holds for the available lists", async () => {
    backend({
      screens: [scr("clock", { name: "Clock" }), scr("idler", { name: "Idler", has_idle: true })],
      settings: { rotation: [], idle_screens: [] },
      status: { idle_supported: true },
    });
    const a = render(<ScreensPage />);
    const s = skeleton(await a.findByRole("group", { name: "Clock" }));
    a.unmount();
    const b = render(<IdlePage />);
    expect(skeleton(await b.findByRole("group", { name: "Idler" }))).toBe(s);
  });
  it("both pages put the same shared actions on an 'in' row, in the same order (Live only adds Pin, in the menu)", async () => {
    backend({
      screens: [scr("a", { name: "A", has_idle: true }), scr("b", { name: "B", has_idle: true, idle_modes: ["mono", "gray4"] })],
      settings: { rotation: ["a", "b"], idle_screens: ["a", "b"] },
      status: { idle_supported: true, idle: { ...status().idle } },
    });
    // the visible row buttons (Settings, More) are shared; the per-page difference lives in the "..." menu
    const rowButtons = (c: HTMLElement) => within(c).getAllByRole("button")
      .map((x) => x.getAttribute("aria-label") ?? x.textContent!.replace(/\s+/g, " ").trim())
      .filter((n) => !/^Move .* to position/.test(n)); // the order-track chevrons are tested in OrderTrack.test.tsx
    const a = render(<ScreensPage />);
    const onLive = rowButtons(await a.findByRole("group", { name: "B" }));
    const liveMenu = within(await a.findByRole("group", { name: "B" })).getByRole("button", { name: "More for B" });
    fireEvent.click(liveMenu);
    const liveMenuItems = within(screen.getByRole("menu")).getAllByRole("menuitem").map((b) => b.textContent);
    a.unmount();
    const b = render(<IdlePage />);
    const onStandby = rowButtons(await b.findByRole("group", { name: "B" }));
    fireEvent.click(within(await b.findByRole("group", { name: "B" })).getByRole("button", { name: "More for B" }));
    const standbyMenuItems = within(screen.getByRole("menu")).getAllByRole("menuitem").map((b) => b.textContent);
    // both rows have "Settings for B" and "More for B" in the same order
    expect(onLive).toEqual(["Settings for B", "More for B"]);
    expect(onStandby).toEqual(onLive);
    // Live's menu adds a Pin entry the Standby one does not; the rest is shared
    expect(liveMenuItems).toContain("Pin to panel");
    expect(standbyMenuItems).not.toContain("Pin to panel");
    expect(liveMenuItems.filter((n) => !/Pin/.test(n!))).toEqual(["Move up", "Move down", "Remove from rotation"]);
    expect(standbyMenuItems).toEqual(["Move up", "Move down", "Remove from standby screens"]);
  });
});
