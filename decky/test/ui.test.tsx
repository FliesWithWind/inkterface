import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Badge, CSS, ScreenThumb, StatusRow, noIdlePreviewReason, noPreviewReason } from "../src/components/ui";
import { PNG } from "./fixtures";
import { __mock } from "./mocks/decky-api";

describe("noPreviewReason", () => {
  const base = { has_active: true, has_code: false, approved: true, enabled: true };
  it("a declarative screen can always be previewed, even when switched off", () => {
    expect(noPreviewReason(base)).toBeNull();
    expect(noPreviewReason({ ...base, enabled: false })).toBeNull();
  });
  it("an idle-only screen is drawn by the panel", () => {
    expect(noPreviewReason({ ...base, has_active: false })).toMatch(/Standby screen: drawn by the panel/);
  });
  it("code needs approval first, then to be switched on", () => {
    expect(noPreviewReason({ ...base, has_code: true, approved: false })).toBe("Needs your approval");
    expect(noPreviewReason({ ...base, has_code: true, enabled: false })).toBe("Switch on to preview");
    expect(noPreviewReason({ ...base, has_code: true })).toBeNull();
  });
});

describe("Badge and StatusRow", () => {
  it("a badge says its meaning in words and hides the icon from assistive tech", () => {
    render(<Badge tone="ok" icon={<svg data-testid="i" />}>In rotation</Badge>);
    expect(screen.getByText("In rotation")).toHaveClass("ink-badge-ok");
    expect(screen.getByTestId("i").parentElement).toHaveAttribute("aria-hidden", "true");
  });
  it("defaults to a neutral tone", () => {
    render(<Badge>Off</Badge>);
    expect(screen.getByText("Off")).toHaveClass("ink-badge-muted");
  });
  it("a status row is a status; an alert row is an alert", () => {
    render(<><StatusRow tone="ok" title="All good">detail</StatusRow><StatusRow tone="error" title="Broken" alert>why</StatusRow></>);
    expect(screen.getByRole("status", { name: "All good" })).toHaveTextContent("detail");
    expect(screen.getByRole("alert")).toHaveTextContent("Brokenwhy");
  });
  it("a status row without detail renders only the title", () => {
    render(<StatusRow tone="muted" title="Quiet" />);
    expect(screen.getByRole("status")).toHaveTextContent(/^Quiet$/);
  });
});

describe("styles", () => {
  it("honours reduced motion and never hides the focus state", () => {
    expect(CSS).toMatch(/prefers-reduced-motion:reduce/);
    expect(CSS).not.toMatch(/outline:\s*none/); // Steam draws the focus ring on the buttons
  });
});

describe("ScreenThumb", () => {
  it("shows the picture once rendered, with a placeholder before", async () => {
    __mock.on("preview_screen", () => ({ ok: true, data_url: PNG }));
    render(<ScreenThumb id="a" name="Alpha" refreshKey="1" reason={null} />);
    expect(screen.getByRole("progressbar", { name: "Rendering Alpha" })).toBeInTheDocument();
    expect(await screen.findByRole("img", { name: "Preview of Alpha" })).toHaveAttribute("src", PNG);
    expect(screen.queryByRole("progressbar")).toBeNull();
  });
  it("a reason replaces the picture and asks the backend for nothing", () => {
    render(<ScreenThumb id="a" name="Alpha" refreshKey="1" reason="Needs your approval" />);
    expect(screen.getByText("Needs your approval")).toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).toBeNull();
    expect(__mock.callsTo("preview_screen")).toEqual([]);
  });
  it("a failure is short in a card and full on the screen's own page", async () => {
    __mock.on("preview_screen", () => ({ ok: false, error: "renderer busy" }));
    const { rerender } = render(<ScreenThumb id="a" name="Alpha" refreshKey="1" reason={null} />);
    expect(await screen.findByText("No preview")).toBeInTheDocument();
    rerender(<ScreenThumb id="b" name="Beta" refreshKey="1" reason={null} detail />);
    expect(await screen.findByText("No preview: renderer busy")).toBeInTheDocument();
  });
  it("renders again when the settings change, keeping the old picture until the new one is there", async () => {
    __mock.on("preview_screen", () => ({ ok: true, data_url: PNG }));
    const { rerender } = render(<ScreenThumb id="a" name="Alpha" refreshKey="1" reason={null} />);
    await screen.findByRole("img");
    rerender(<ScreenThumb id="a" name="Alpha" refreshKey="2" reason={null} />);
    expect(screen.getByRole("img")).toBeInTheDocument();
    await waitFor(() => expect(__mock.callsTo("preview_screen")).toHaveLength(2));
  });
  it("never shows the old screen's picture under a new screen", async () => {
    const slow: Record<string, (v: unknown) => void> = {};
    __mock.on("preview_screen", (id: string) => (id === "a" ? { ok: true, data_url: PNG } : new Promise((r) => { slow[id] = r; })));
    const { rerender } = render(<ScreenThumb id="a" name="Alpha" refreshKey="1" reason={null} />);
    expect(await screen.findByRole("img", { name: "Preview of Alpha" })).toBeInTheDocument();
    rerender(<ScreenThumb id="b" name="Beta" refreshKey="1" reason={null} />); // same component, another screen
    expect(screen.queryByRole("img")).toBeNull(); // not Alpha's picture
    expect(screen.getByRole("progressbar", { name: "Rendering Beta" })).toBeInTheDocument();
    slow.b({ ok: true, data_url: "data:image/png;base64,BBBB" });
    expect(await screen.findByRole("img", { name: "Preview of Beta" })).toHaveAttribute("src", "data:image/png;base64,BBBB");
  });
  it("sizes itself when asked", () => {
    const { container } = render(<ScreenThumb id="a" name="Alpha" refreshKey="1" reason="x" width={300} />);
    expect((container.firstChild as HTMLElement).style.getPropertyValue("--ink-thumb-w")).toBe("300px");
  });
});

describe("ScreenThumb for idle layouts", () => {
  it("asks the idle renderer for the given mode, and never the active one", async () => {
    __mock.on("preview_idle_screen", () => ({ ok: true, data_url: PNG }));
    render(<ScreenThumb id="w" name="Weather" refreshKey="gray4" reason={null} idle={{ mode: "gray4" }} />);
    expect(await screen.findByRole("img", { name: "Preview of Weather" })).toHaveAttribute("src", PNG);
    expect(__mock.callsTo("preview_idle_screen")).toEqual([["w", "gray4"]]);
    expect(__mock.callsTo("preview_screen")).toEqual([]);
  });
  it("null mode means the user's choice", async () => {
    __mock.on("preview_idle_screen", () => ({ ok: true, data_url: PNG }));
    render(<ScreenThumb id="w" name="Weather" refreshKey="x" reason={null} idle={{ mode: null }} />);
    await screen.findByRole("img");
    expect(__mock.callsTo("preview_idle_screen")).toEqual([["w", null]]);
  });
  it("renders again when the mode changes", async () => {
    __mock.on("preview_idle_screen", () => ({ ok: true, data_url: PNG }));
    const { rerender } = render(<ScreenThumb id="w" name="Weather" refreshKey="mono" reason={null} idle={{ mode: "mono" }} />);
    await screen.findByRole("img");
    rerender(<ScreenThumb id="w" name="Weather" refreshKey="gray4" reason={null} idle={{ mode: "gray4" }} />);
    await waitFor(() => expect(__mock.callsTo("preview_idle_screen")).toEqual([["w", "mono"], ["w", "gray4"]]));
  });
  it("a failure is a short note; a reason asks the backend for nothing", async () => {
    __mock.on("preview_idle_screen", () => ({ ok: false, error: "idle preview renderer is not installed" }));
    const { rerender } = render(<ScreenThumb id="w" name="Weather" refreshKey="1" reason={null} idle={{ mode: "mono" }} />);
    expect(await screen.findByText("No preview")).toBeInTheDocument();
    __mock.calls.length = 0;
    rerender(<ScreenThumb id="c" name="Code" refreshKey="1" reason="Needs your approval" idle={{ mode: "mono" }} />);
    expect(screen.getByText("Needs your approval")).toBeInTheDocument();
    expect(__mock.callsTo("preview_idle_screen")).toEqual([]);
  });
});

describe("ScreenThumb with a custom renderer", () => {
  it("uses the given render function instead of the active or idle renderer", async () => {
    const render2 = (id: string) => Promise.resolve({ ok: true as const, data_url: `data:img/${id}` });
    render(<ScreenThumb id="clock" name="Clock" refreshKey="1" reason={null} render={render2} />);
    expect(await screen.findByRole("img", { name: "Preview of Clock" })).toHaveAttribute("src", "data:img/clock");
    expect(__mock.callsTo("preview_screen")).toEqual([]);
    expect(__mock.callsTo("preview_idle_screen")).toEqual([]);
  });
  it("a reason still skips rendering even with a renderer", () => {
    const render2 = vi.fn(() => Promise.resolve({ ok: true as const, data_url: PNG }));
    render(<ScreenThumb id="clock" name="Clock" refreshKey="1" reason="Incompatible" render={render2} />);
    expect(screen.getByText("Incompatible")).toBeInTheDocument();
    expect(render2).not.toHaveBeenCalled();
  });
});

describe("noIdlePreviewReason", () => {
  const base = { has_idle: true, has_code: false, approved: true };
  it("has a picture for any idle layout, code or not once approved", () => {
    expect(noIdlePreviewReason(base)).toBeNull();
    expect(noIdlePreviewReason({ ...base, has_code: true })).toBeNull();
  });
  it("code waits for approval; no layout, no picture", () => {
    expect(noIdlePreviewReason({ ...base, has_code: true, approved: false })).toBe("Needs your approval");
    expect(noIdlePreviewReason({ ...base, has_idle: false })).toBe("No standby layout");
  });
});
