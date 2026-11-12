// Parity: the Browse sheet and the Installed sheet show the same buttons for the same installed screen
// (components/PackageActions): same labels, order, primary and disabled state.
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StorePage } from "../src/pages/StorePage";
import { STORE_REINSTALL_NOTE } from "../src/names";
import { backend, catalogEntry as ent, screen as scr } from "./fixtures";
import { __mock } from "./mocks/decky-api";
import type { CatalogEntry, ScreenInfo } from "../src/api";

const SHA = "ef".repeat(32);
const base = scr("deco", { name: "Deco", builtin: false, author: "Ada", version: "1.2.0" });

/** One row per footer button: "label", "*" when primary, "(off)" when disabled. */
const bar = (name: string) =>
  Array.from(screen.getByRole("dialog", { name }).querySelectorAll<HTMLButtonElement>(".ink-sheet-f button")).map(
    (b) => `${(b.textContent ?? "").replace(/\s+/g, " ").trim()}${b.classList.contains("ink-btn-primary") ? " *" : ""}${b.disabled ? " (off)" : ""}`,
  );

const setup = (s: ScreenInfo, e: CatalogEntry) => {
  backend({ screens: [s] });
  __mock.on("catalog_list", () => ({ ok: true, entries: [e], fetched_at: 1_700_000_000, url: "https://cat/" }));
  __mock.on("catalog_refresh", () => ({ ok: true, fetched_at: 1_700_000_000, count: 1 }));
  render(<StorePage />);
};

const browseBar = async (want: string[]) => {
  fireEvent.click(await screen.findByRole("group", { name: "Deco" }));
  await screen.findByRole("dialog", { name: "Deco" });
  await waitFor(() => expect(bar("Deco")).toEqual(want)); // the sheet loads list_screens itself
  const got = bar("Deco");
  fireEvent.keyDown(screen.getByRole("dialog", { name: "Deco" }), { key: "Escape" });
  return got;
};

const installedBar = async (update: boolean) => {
  fireEvent.click(screen.getByRole("tab", { name: "Installed" }));
  const c = await screen.findByRole("group", { name: "Deco" });
  if (update) await within(c).findByText(/^Update /); // the updates map loads from the catalog cache
  fireEvent.click(c);
  await screen.findByRole("dialog", { name: "Deco" });
  return bar("Deco");
};

const U = "Uninstall Deco";
const cases: [string, Partial<ScreenInfo>, Partial<CatalogEntry>, string[]][] = [
  ["live layout, not in Live", {}, {}, ["Add to Live Deco *", U]],
  ["in Live already: only Uninstall, nothing primary", { in_rotation: true }, {}, [U]],
  ["update available", {}, { version: "1.3.0", update_available: true }, ["Update to 1.3.0 Deco *", "Add to Live Deco", U]],
  ["incompatible update", { in_rotation: true }, { version: "1.3.0", update_available: true, compatible: false, incompatible_reason: "needs plugin 9" }, ["Update to 1.3.0 Deco * (off)", U]],
  ["needs approval", { has_code: true, code_sha256: SHA, approved: false, needs_approval: true, enabled: false }, { has_code: true, needs_approval: true }, ["Review and approve Deco *", U]],
  ["needs approval and an update", { has_code: true, code_sha256: SHA, approved: false, needs_approval: true, enabled: false }, { has_code: true, needs_approval: true, version: "1.3.0", update_available: true }, ["Update to 1.3.0 Deco *", "Review and approve Deco", U]],
  ["standby-only layout", { has_active: false, has_idle: true }, { has_active: false, has_idle: true }, ["Add to Standby Deco *", U]],
  ["standby-only, in Standby", { has_active: false, has_idle: true, in_idle: true }, { has_active: false, has_idle: true }, [U]],
  ["both layouts, in Live only", { has_idle: true, in_rotation: true }, { has_idle: true }, ["Add to Standby Deco *", U]],
  ["both layouts, unused", { has_idle: true }, { has_idle: true }, ["Add to Live Deco *", "Add to Standby Deco", U]],
];

describe("Browse and Installed sheets show the same actions", () => {
  it.each(cases)("%s", async (_n, so, eo, want) => {
    setup({ ...base, ...so }, ent("deco", { name: "Deco", installed: true, installed_version: "1.2.0", version: "1.2.0", ...eo }));
    const browse = await browseBar(want);
    const installed = await installedBar(!!eo.update_available);
    expect(installed).toEqual(want);
    expect(browse).toEqual(installed);
  });

  it("not installed: Browse shows Install only, primary and focused", async () => {
    setup(scr("telemetry"), ent("deco", { name: "Deco" }));
    fireEvent.click(await screen.findByRole("group", { name: "Deco" }));
    await screen.findByRole("dialog", { name: "Deco" });
    expect(bar("Deco")).toEqual(["Install Deco *"]);
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Install Deco" }));
  });

  it("Uninstall from Browse warns that the screen is in Live, then uninstalls", async () => {
    setup({ ...base, in_rotation: true }, ent("deco", { name: "Deco", installed: true, installed_version: "1.2.0" }));
    fireEvent.click(await screen.findByRole("group", { name: "Deco" }));
    await screen.findByRole("dialog", { name: "Deco" });
    await waitFor(() => expect(bar("Deco")).toEqual([U]));
    fireEvent.click(screen.getByRole("button", { name: U }));
    const d = await screen.findByRole("dialog", { name: "Uninstall “Deco”?" });
    expect(d).toHaveTextContent(`Deco is in your Live rotation. Uninstalling takes it out of there too. ${STORE_REINSTALL_NOTE}`);
    expect(__mock.callsTo("uninstall_package")).toEqual([]);
    fireEvent.click(within(d).getByText("Uninstall"));
    await waitFor(() => expect(__mock.callsTo("uninstall_package")).toEqual([["deco"]]));
    expect(await screen.findByText("Deco uninstalled.")).toBeInTheDocument();
  });
});
