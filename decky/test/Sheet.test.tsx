// The sheet has no Done button (B closes it), its primary action gets focus first, and focus goes back to the opener on close.
import { fireEvent, render, screen, within } from "@testing-library/react";
import { ReactElement } from "react";
import { describe, expect, it } from "vitest";
import { DialogButton, Focusable } from "@decky/ui";
import { openSheet } from "../src/components/Sheet";
import { StorePage } from "../src/pages/StorePage";
import { backend, catalogEntry as ent } from "./fixtures";
import { __mock } from "./mocks/decky-api";

const bar = (...b: ReactElement[]) => <Focusable className="ink-bar">{b}</Focusable>;

describe("Sheet", () => {
  it("has no Done button, with or without its own actions", () => {
    openSheet({ title: "Plain", children: <p>body</p> });
    const d = screen.getByRole("dialog", { name: "Plain" });
    expect(within(d).queryByRole("button")).toBeNull();
    expect(d.querySelector(".ink-sheet-f")).toBeNull(); // no empty footer either
    fireEvent.keyDown(d, { key: "Escape" });
    openSheet({ title: "Act", footer: bar(<DialogButton key="a" className="ink-btn ink-btn-primary">Install</DialogButton>) });
    expect(within(screen.getByRole("dialog", { name: "Act" })).getAllByRole("button").map((b) => b.textContent)).toEqual(["Install"]);
  });

  it("B (Escape) closes it", () => {
    openSheet({ title: "Close me" });
    fireEvent.keyDown(screen.getByRole("dialog", { name: "Close me" }), { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "Close me" })).toBeNull();
  });

  it("focuses the primary action on open, even when it is not the first button", () => {
    openSheet({
      title: "P",
      children: <button>in body</button>,
      footer: bar(<DialogButton key="u" className="ink-btn">Uninstall</DialogButton>, <DialogButton key="i" className="ink-btn ink-btn-primary">Update</DialogButton>),
    });
    expect(document.activeElement).toHaveTextContent("Update");
  });

  it("skips a disabled primary and falls back to the first enabled control", () => {
    openSheet({
      title: "D",
      children: <input aria-label="field" />,
      footer: bar(<DialogButton key="i" className="ink-btn ink-btn-primary" disabled>Install</DialogButton>),
    });
    expect(document.activeElement).toBe(screen.getByLabelText("field"));
  });

  it("gives focus back to the control that opened it", () => {
    render(<button onClick={() => openSheet({ title: "Back", footer: bar(<DialogButton key="x" className="ink-btn ink-btn-primary">Go</DialogButton>) })}>opener</button>);
    const opener = screen.getByRole("button", { name: "opener" });
    opener.focus();
    fireEvent.click(opener);
    expect(document.activeElement).toHaveTextContent("Go");
    fireEvent.keyDown(screen.getByRole("dialog", { name: "Back" }), { key: "Escape" });
    expect(document.activeElement).toBe(opener);
  });
});

describe("Store detail popup (Install + Done)", () => {
  it("shows Install as the only, primary, focused button", async () => {
    backend();
    __mock.on("catalog_list", () => ({ ok: true, entries: [ent("gpu-focus", { name: "GPU Focus" })], fetched_at: 1, url: "u" }));
    __mock.on("catalog_refresh", () => ({ ok: true, fetched_at: 1, count: 1 }));
    render(<StorePage />);
    fireEvent.click(await screen.findByRole("group", { name: "GPU Focus" }));
    const d = within(await screen.findByRole("dialog", { name: "GPU Focus" }));
    expect(d.queryByRole("button", { name: "Done" })).toBeNull();
    const install = d.getByRole("button", { name: /Install/ });
    expect(d.getAllByRole("button")).toEqual([install]);
    expect(install).toHaveClass("ink-btn-primary");
    expect(document.activeElement).toBe(install);
  });
});
