import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MainPage } from "../src/MainPage";
import { NAMES, SIDEBAR } from "../src/names";
import { backend } from "./fixtures";

describe("MainPage", () => {
  it("has the six pages in the agreed order and starts on Live Screens", async () => {
    backend();
    render(<MainPage />);
    expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual(["Live Screens", "Standby Screens", "Screen Store", "Firmware", "Settings", "About"]);
    expect([...SIDEBAR]).toEqual(screen.getAllByRole("tab").map((t) => t.textContent));
    expect(await screen.findByRole("region", { name: "In rotation" })).toBeInTheDocument();
  });

  it("uses none of the old names in the sidebar", () => {
    backend();
    render(<MainPage />);
    const tabs = screen.getAllByRole("tab").map((t) => t.textContent);
    for (const old of ["Screens", "Idle", "Packages"]) expect(tabs).not.toContain(old);
  });

  it.each([
    [NAMES.standby, "Overview"],
    [NAMES.store, "Screen Store"],
    [NAMES.settings, "Connections"],
    [NAMES.firmware, "Firmware"],
    [NAMES.about, "About Inkterface"],
  ])("opens %s", async (tab, heading) => {
    backend();
    render(<MainPage />);
    fireEvent.click(screen.getByRole("tab", { name: tab }));
    expect(await screen.findByRole("region", { name: heading })).toBeInTheDocument();
  });
});
