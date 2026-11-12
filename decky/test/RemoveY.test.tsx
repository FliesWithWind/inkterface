// Buttons: A only activates the focused control, Y removes the focused row (with Undo), Menu opens "...",
// the chevrons are ordinary DialogButtons, previews have no bezel.
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AvailableList, OrderedList, Slots, UNDO_MS } from "../src/components/ScreenList";
import { ScreenThumb, Styles } from "../src/components/ui";
import { CSS } from "../src/components/styles";
import { IdlePage } from "../src/pages/IdlePage";
import { ScreensPage } from "../src/pages/ScreensPage";
import { backend, screen as info } from "./fixtures";
import { __mock } from "./mocks/decky-api";

const NAMES = ["Telemetry", "Now Playing", "Thermals & Power", "Weather"];
const ID = (n: string) => n.toLowerCase().replace(/ & /g, "-").replace(" ", "-");
const items = NAMES.map((n) => info(ID(n), { name: n }));
const slots: Slots = { thumb: (s) => <div>pic {s.name}</div>, sheet: (s) => <div>settings body of {s.name}</div> };

const row = (name: string) => screen.getByRole("group", { name });
const rowNames = () => screen.getAllByRole("group").filter((g) => g.hasAttribute("data-row-id")).map((g) => g.getAttribute("aria-label"));
const pad = (el: Element, key: string) => fireEvent.keyDown(el, { key });
const ctl = (name: string, c: string) => row(name).querySelector<HTMLElement>(`[data-ctl="${c}"]`)!;

function Harness({ start = items, onChange = vi.fn(), result = { ok: true }, minItems = 0 }: {
  start?: typeof items; onChange?: (ids: string[]) => void; result?: { ok: boolean }; minItems?: number;
}) {
  const [list, setList] = useState(start);
  return (
    <OrderedList
      items={list}
      slots={slots}
      empty="nothing"
      removeLabel="Remove from rotation"
      outOf="rotation"
      minItems={minItems}
      minNote="the rotation needs at least one screen"
      onChange={async (ids) => {
        onChange(ids);
        if (result.ok) setList(ids.map((id) => items.find((x) => x.id === id)!));
        return result;
      }}
    />
  );
}

afterEach(() => vi.useRealTimers());

describe("A only activates the focused control", () => {
  it("A on the row opens nothing and the row maps nothing to A", () => {
    render(<Harness />);
    pad(row("Weather"), "Enter");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(row("Weather").hasAttribute("data-ok")).toBe(false); // Steam's footer then says A SELECT
    expect(row("Weather").querySelector(".ink-hints")).not.toHaveTextContent("Settings");
  });

  it("A on the Settings button still opens the sheet", () => {
    render(<Harness />);
    fireEvent.click(ctl("Weather", "settings"));
    expect(screen.getByRole("dialog", { name: "Weather" })).toBeInTheDocument();
  });

  it("A on a chevron moves the row and opens nothing", () => {
    render(<Harness />);
    fireEvent.click(ctl("Weather", "up"));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(rowNames().indexOf("Weather")).toBe(2);
  });

  it("Live and Standby pages: A on a row opens no sheet, and the footer text is Steam's default", async () => {
    backend({ screens: [info("telemetry"), info("clock")], settings: { rotation: ["telemetry", "clock"] } });
    const { unmount } = render(<ScreensPage />);
    const r = await screen.findByRole("group", { name: "Clock" });
    pad(r, "Enter");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(r.hasAttribute("data-ok")).toBe(false);
    unmount();
    backend({ screens: [info("weather", { has_idle: true, in_idle: true, idle_modes: ["mono", "gray4"] })], settings: { idle_screens: ["weather"] }, status: { idle_supported: true } });
    render(<IdlePage />);
    const w = await screen.findByRole("group", { name: "Weather" });
    pad(w, "Enter");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(w.hasAttribute("data-ok")).toBe(false);
  });
});

describe("Y removes the focused row, with Undo", () => {
  it("Y takes the row out, sends the order without it, and says so with a focusable Undo", async () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    pad(row("Now Playing"), "y");
    expect(onChange).toHaveBeenCalledWith(["telemetry", "thermals-power", "weather"]);
    await waitFor(() => expect(rowNames()).toEqual(["Telemetry", "Thermals & Power", "Weather"]));
    const note = screen.getByRole("status");
    expect(note).toHaveTextContent(/Now Playing removed from rotation\s*·\s*Undo/);
    expect(within(note).getByRole("button", { name: "Undo" })).toBeEnabled();
  });

  it("hint bar and Steam's footer say Y Remove; the Menu button says More", () => {
    render(<Harness />);
    expect(row("Weather").querySelector(".ink-hints")).toHaveTextContent("YRemove");
    expect(row("Weather").querySelector(".ink-hints")).not.toHaveTextContent("YMore");
    expect(row("Weather").getAttribute("data-options")).toBe("Remove");
    expect(row("Weather").getAttribute("data-menu")).toBe("More");
  });

  it("Undo puts the screen back at the same position and tells the backend", async () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    pad(row("Now Playing"), "y");
    await waitFor(() => expect(rowNames()).not.toContain("Now Playing"));
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect(onChange).toHaveBeenLastCalledWith(["telemetry", "now-playing", "thermals-power", "weather"]);
    await waitFor(() => expect(rowNames()).toEqual(NAMES));
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("Undo restores the first and the last position too", async () => {
    render(<Harness />);
    pad(row("Weather"), "y");
    await waitFor(() => expect(rowNames()).not.toContain("Weather"));
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    await waitFor(() => expect(rowNames()).toEqual(NAMES));
    pad(row("Telemetry"), "y");
    await waitFor(() => expect(rowNames()).not.toContain("Telemetry"));
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    await waitFor(() => expect(rowNames()).toEqual(NAMES));
  });

  it("focus moves to the next row (same control), so it never falls out of the list", async () => {
    render(<Harness />);
    ctl("Now Playing", "settings").focus();
    pad(ctl("Now Playing", "settings"), "y");
    await waitFor(() => expect(rowNames()).not.toContain("Now Playing"));
    expect(document.activeElement).toBe(ctl("Thermals & Power", "settings"));
  });

  it("removing the last row moves focus to the previous one", async () => {
    render(<Harness />);
    ctl("Weather", "more").focus();
    pad(ctl("Weather", "more"), "y");
    await waitFor(() => expect(rowNames()).not.toContain("Weather"));
    expect(document.activeElement).toBe(ctl("Thermals & Power", "more"));
  });

  it("a disabled neighbour control falls back to a live one (first row's chevron)", async () => {
    render(<Harness />);
    ctl("Now Playing", "up").focus();
    pad(ctl("Now Playing", "up"), "y"); // Thermals is next; its up is enabled
    await waitFor(() => expect(rowNames()).not.toContain("Now Playing"));
    expect(document.activeElement).toBe(ctl("Thermals & Power", "up"));
    pad(ctl("Telemetry", "up"), "y"); // Telemetry (first) removed while its up chevron is the held control -> next row
  });

  it("removing the only row of a list that may be empty moves focus to Undo", async () => {
    render(<Harness start={[items[0]]} />);
    ctl("Telemetry", "settings").focus();
    pad(ctl("Telemetry", "settings"), "y");
    await waitFor(() => expect(screen.getByText("nothing")).toBeInTheDocument());
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Undo" }));
  });

  it("the last screen of the rotation is refused with a clear message: no change, no Undo", () => {
    const onChange = vi.fn();
    render(<Harness start={[items[0]]} onChange={onChange} minItems={1} />);
    pad(row("Telemetry"), "y");
    expect(onChange).not.toHaveBeenCalled();
    expect(rowNames()).toEqual(["Telemetry"]);
    expect(screen.getByRole("status")).toHaveTextContent("Telemetry cannot be removed: the rotation needs at least one screen.");
    expect(screen.queryByRole("button", { name: "Undo" })).toBeNull();
    const y = Array.from(row("Telemetry").querySelectorAll<HTMLElement>(".ink-hint")).find((h) => h.textContent === "YRemove")!;
    expect(y.dataset.off).toBe("true"); // dimmed: Y cannot do anything here
  });

  it("a refused change (backend says no) puts the row back and drops the note", async () => {
    render(<Harness result={{ ok: false }} />);
    pad(row("Now Playing"), "y");
    await waitFor(() => expect(rowNames()).toEqual(NAMES));
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("the '...' menu Remove takes the same path (note + Undo)", async () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole("button", { name: "More for Weather" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Remove from rotation" }));
    await waitFor(() => expect(rowNames()).not.toContain("Weather"));
    expect(screen.getByRole("status")).toHaveTextContent(/Weather removed from rotation\s*·\s*Undo/);
  });

  it("two quick removals each send the order built on the one before (no stale re-add)", async () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    pad(row("Weather"), "y");
    pad(row("Telemetry"), "y");
    expect(onChange).toHaveBeenNthCalledWith(1, ["telemetry", "now-playing", "thermals-power"]);
    expect(onChange).toHaveBeenNthCalledWith(2, ["now-playing", "thermals-power"]);
  });

  it("another action clears the note; it also goes away by itself after a few seconds, but not while Undo has focus", async () => {
    vi.useFakeTimers();
    render(<Harness />);
    pad(row("Weather"), "y");
    act(() => { vi.advanceTimersByTime(UNDO_MS - 100); });
    expect(screen.getByRole("status")).toBeInTheDocument();
    screen.getByRole("button", { name: "Undo" }).focus();
    act(() => { vi.advanceTimersByTime(UNDO_MS); });
    expect(screen.getByRole("status")).toBeInTheDocument(); // held while focused
    (document.activeElement as HTMLElement).blur();
    act(() => { vi.advanceTimersByTime(UNDO_MS); });
    expect(screen.queryByRole("status")).toBeNull();
    pad(row("Now Playing"), "y");
    expect(screen.getByRole("status")).toBeInTheDocument();
    fireEvent.click(within(row("Telemetry")).getByRole("button", { name: /^Move Telemetry down/ })); // the next action replaces it
    expect(screen.getByRole("status")).toHaveTextContent("Telemetry is now 2 of 2");
  });

  it("Y does nothing on an Available row", () => {
    const onAdd = vi.fn();
    render(<AvailableList items={items} slots={slots} onAdd={onAdd} />);
    pad(row("Weather"), "y");
    expect(onAdd).not.toHaveBeenCalled();
    expect(screen.queryByRole("status")).toBeNull();
    expect(row("Weather").hasAttribute("data-options")).toBe(false);
  });

  it("a locked list (disabled) ignores Y", () => {
    const onChange = vi.fn();
    render(<OrderedList items={items} slots={slots} disabled empty="x" removeLabel="r" onChange={onChange} />);
    pad(row("Weather"), "y");
    expect(onChange).not.toHaveBeenCalled();
  });
});

describe("Y on the pages", () => {
  const calls = (n: string) => __mock.callsTo(n);
  beforeEach(() => undefined);

  it("Live: Y removes from the rotation (set_rotation without it); Undo restores the position", async () => {
    backend({ screens: [info("telemetry"), info("clock"), info("extra")], settings: { rotation: ["telemetry", "clock", "extra"], rotation_interval_s: 20 } });
    render(<ScreensPage />);
    pad(await screen.findByRole("group", { name: "Clock" }), "y");
    await waitFor(() => expect(calls("set_rotation")[0]).toEqual([["telemetry", "extra"], 20]));
    expect(await screen.findByRole("status")).toHaveTextContent(/Clock removed from rotation\s*·\s*Undo/);
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    await waitFor(() => expect(calls("set_rotation")[1]).toEqual([["telemetry", "clock", "extra"], 20]));
  });

  it("Live: Y on the last screen is refused before the backend is asked (it would refuse an empty rotation)", async () => {
    backend({ screens: [info("telemetry"), info("clock")], settings: { rotation: ["clock"] } });
    render(<ScreensPage />);
    pad(await screen.findByRole("group", { name: "Clock" }), "y");
    expect(await screen.findByRole("status")).toHaveTextContent("Clock cannot be removed: the rotation needs at least one screen.");
    expect(calls("set_rotation")).toEqual([]);
  });

  it("Live: Y on an Available row does nothing", async () => {
    backend({ screens: [info("telemetry"), info("clock")], settings: { rotation: ["telemetry"] } });
    render(<ScreensPage />);
    pad(await screen.findByRole("group", { name: "Clock" }), "y");
    expect(calls("set_rotation")).toEqual([]);
    expect(calls("set_screen_enabled")).toEqual([]);
  });

  it("Standby: Y removes from the selected list; Undo puts it back where it was", async () => {
    const mk = (id: string, name: string) => info(id, { name, has_idle: true, idle_modes: ["mono"], idle_mode: "mono" });
    backend({ screens: [mk("a", "Alpha"), mk("b", "Beta"), mk("c", "Gamma")], settings: { idle_screens: ["a", "b", "c"] }, status: { idle_supported: true } });
    render(<IdlePage />);
    pad(await screen.findByRole("group", { name: "Beta" }), "y");
    await waitFor(() => expect(__mock.callsTo("set_settings")[0][0]).toEqual({ idle_screens: ["a", "c"] }));
    expect(await screen.findByText(/Beta removed from standby screens/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    await waitFor(() => expect(__mock.callsTo("set_settings")[1][0]).toEqual({ idle_screens: ["a", "b", "c"] }));
  });

  it("Standby may end up empty (the backend allows it)", async () => {
    const mk = (id: string, name: string) => info(id, { name, has_idle: true, idle_modes: ["mono"], idle_mode: "mono" });
    backend({ screens: [mk("a", "Alpha")], settings: { idle_screens: ["a"] }, status: { idle_supported: true } });
    render(<IdlePage />);
    pad(await screen.findByRole("group", { name: "Alpha" }), "y");
    await waitFor(() => expect(__mock.callsTo("set_settings")[0][0]).toEqual({ idle_screens: [] }));
  });
});

describe("the chevrons are ordinary DialogButtons (one focus look)", () => {
  it("up / down carry the same classes as Settings and '...' (ink-btn), plus only their size class", () => {
    render(<Harness />);
    const r = row("Now Playing");
    const classes = (el: HTMLElement) => el.className.split(" ").filter(Boolean);
    const settings = classes(ctl("Now Playing", "settings"));
    expect(settings).toContain("ink-btn");
    for (const c of ["up", "down"]) {
      const k = classes(ctl("Now Playing", c));
      expect(k).toContain("ink-btn");
      expect(k).toContain("ink-btn-ico"); // the same icon-button class as "..."
      expect(k.filter((x) => !settings.includes(x))).toEqual(["ink-btn-ico", "ink-ord-btn"]);
      expect(ctl("Now Playing", c).tagName).toBe(ctl("Now Playing", "settings").tagName);
    }
    expect(classes(ctl("Now Playing", "more"))).toContain("ink-btn-ico");
    expect(r).toBeTruthy();
  });

  it("the stylesheet gives chevrons no colour or background of their own (no blue focus), and no custom focus rule", () => {
    const rules = CSS.split("\n").filter((l) => l.includes(".ink-ord-btn"));
    for (const l of rules) {
      expect(l).not.toMatch(/background|color:|outline|focus|gpfocus|BLUE|26,159,255|1a9fff/i);
    }
    expect(CSS).not.toMatch(/\.ink-ord-btn[^{]*:focus/);
  });

  it("every button that sets its own background gets Steam's white focus fill back; cards use the white ring, not blue", () => {
    expect(CSS).toMatch(/\.ink-btn-primary\.gpfocus[^}]*background:#fff !important;color:#23262e !important/);
    expect(CSS).toMatch(/\.ink-tab\[aria-selected="true"\]\.gpfocus/);
    expect(CSS).toMatch(/\.ink-link\.gpfocus/);
    const cap = CSS.split("\n").filter((l) => l.includes(".ink-cap:hover") || l.includes("0 0 0 3px #fff,0 12px 22px"));
    expect(cap.join("\n")).toContain("0 0 0 3px #fff");
    expect(CSS).not.toMatch(/\.ink-cap[^}]*0 0 0 3px \$\{BLUE\}|0 0 18px 2px rgba\(26,159,255/);
  });

  it("the disabled look is Steam's own: the stylesheet adds no opacity for disabled buttons", () => {
    expect(CSS).not.toMatch(/:disabled\{[^}]*opacity/);
    expect(CSS).not.toMatch(/\.Disabled\{[^}]*opacity/);
  });

  it("a Settings button with nothing to configure is disabled and not focusable (the D-pad skips it)", () => {
    render(<OrderedList items={items} slots={{ thumb: slots.thumb }} empty="x" removeLabel="r" onChange={() => undefined} />);
    const b = ctl("Weather", "settings");
    expect(b).toBeDisabled();
    expect(b.getAttribute("data-focusable")).toBe("false");
    expect(b.getAttribute("aria-disabled")).toBe("true");
  });
});

describe("previews have no bezel", () => {
  it("a thumbnail is the plain image in a 1 px bordered box: no bezel markup, no LED", async () => {
    __mock.reset?.();
    backend({ screens: [info("clock")], settings: { rotation: ["clock"] } });
    const { container } = render(<><Styles /><ScreensPage /></>);
    await screen.findByRole("group", { name: "Clock" });
    await waitFor(() => expect(container.querySelector(".ink-pic img")).not.toBeNull());
    expect(container.querySelector(".ink-bz, .ink-bz-glass, .ink-bz-led")).toBeNull();
    expect(container.innerHTML).not.toMatch(/ink-bz/);
    for (const img of Array.from(container.querySelectorAll(".ink-pic img"))) expect(img.parentElement?.className).toBe("ink-pic");
  });

  it("the stylesheet has a 1 px subtle border, keeps 648/480, scales crisp, and no bezel rule", () => {
    expect(CSS).not.toMatch(/ink-bz|bezel faceplate|ink-bz-led/);
    const pic = CSS.split("\n").filter((l) => l.includes(".ink-pic")).join("\n");
    expect(pic).toMatch(/aspect-ratio:648\/480/);
    expect(pic).toMatch(/border:1px solid rgba\(255,255,255,\.\d+\)/);
    expect(pic).toMatch(/image-rendering:pixelated/);
  });

  it("ScreenThumb with a reason shows the reason in the plain box", () => {
    const { container } = render(<ScreenThumb id="x" name="X" refreshKey="k" reason="Needs your approval" />);
    expect(container.querySelector(".ink-pic")).toHaveTextContent("Needs your approval");
    expect(container.querySelector(".ink-bz")).toBeNull();
  });
});

describe("the settings sheet header", () => {
  it("the picture is as wide as its 110 px box and the title column can shrink and wrap (long names)", () => {
    expect(CSS).toMatch(/\.ink-sheet-art \.ink-thumb\{width:100%\}/);
    expect(CSS).toMatch(/\.ink-sheet-h>div:not\(\.ink-sheet-art\)\{[^}]*min-width:0[^}]*overflow-wrap:anywhere/);
  });
});
