import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { CODE_WARNING } from "../src/components/ApprovalDialog";
import { StorePage, savedAgo } from "../src/pages/StorePage";
import { NAMES, STORE_REINSTALL_NOTE } from "../src/names";
import { Navigation } from "./mocks/decky-ui";
import { PNG, backend, catalogEntry as ent, screen as scr } from "./fixtures";
import { __mock } from "./mocks/decky-api";

const calls = (n: string) => __mock.callsTo(n);
const card = (name: string) => screen.findByRole("group", { name });
const SHA = "ab".repeat(32);
const SHA2 = "cd".repeat(32);

/** Register the catalog list that catalog_refresh + the two catalog_list reads return. */
const catalog = (entries: ReturnType<typeof ent>[], o: { fetched_at?: number | null } = {}) => {
  const fetched_at = "fetched_at" in o ? o.fetched_at! : 1_700_000_000;
  __mock.on("catalog_list", () => ({ ok: true, entries, fetched_at, url: "https://cat/" }));
  __mock.on("catalog_refresh", () => ({ ok: true, fetched_at: fetched_at ?? 1_700_000_000, count: entries.length }));
};

/** Open a card's details sheet and return a `within` bound to it. */
const openSheet = async (name: string) => {
  fireEvent.click(await card(name));
  return within(await screen.findByRole("dialog"));
};

describe("savedAgo", () => {
  it.each([
    [1_700_000_000, 1_700_000_000_000 + 30_000, "just now"],
    [1_700_000_000, 1_700_000_000_000 + 120_000, "2 min ago"],
    [1_700_000_000, 1_700_000_000_000 + 3 * 3_600_000, "3 h ago"],
  ])("%i", (at, now, text) => expect(savedAgo(at, now)).toBe(text));
});

describe("StorePage tabs", () => {
  it("has a Browse and an Installed tab; Browse is the default", async () => {
    backend();
    catalog([ent("clock", { name: "Clock" })]);
    render(<StorePage />);
    const browse = screen.getByRole("tab", { name: "Browse" });
    const installed = screen.getByRole("tab", { name: "Installed" });
    expect(browse).toHaveAttribute("aria-selected", "true");
    expect(installed).toHaveAttribute("aria-selected", "false");
    expect(await card("Clock")).toBeInTheDocument();
  });

  it("switches to the Installed tab and back", async () => {
    backend({ screens: [scr("deco", { name: "Deco", builtin: false, author: "Ada", version: "1.2.0" })] });
    catalog([ent("clock", { name: "Clock" })]);
    render(<StorePage />);
    fireEvent.click(screen.getByRole("tab", { name: "Installed" }));
    expect(screen.getByRole("tab", { name: "Installed" })).toHaveAttribute("aria-selected", "true");
    expect(await card("Deco")).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Clock" })).toBeNull();
    fireEvent.click(screen.getByRole("tab", { name: "Browse" }));
    expect(await card("Clock")).toBeInTheDocument();
  });

  it("uses the plugin's one tab strip (same classes as the Live Screens lists)", () => {
    backend();
    catalog([]);
    render(<StorePage />);
    const list = screen.getByRole("tablist", { name: NAMES.store });
    expect(list).toHaveClass("ink-tabs");
    expect(within(list).getAllByRole("tab").map((t) => t.className.split(" ").filter((c) => c.startsWith("ink-")))).toEqual([["ink-tab"], ["ink-tab"]]);
  });
});

describe("StorePage Browse load and refresh", () => {
  it("shows the cache first, then the refreshed catalog", async () => {
    backend();
    __mock.on("catalog_list", () => ({ ok: true, entries: [ent("clock", { name: "Clock" })], fetched_at: 1_700_000_000, url: "https://cat/" }));
    __mock.on("catalog_refresh", () => ({ ok: true, fetched_at: 1_700_000_100, count: 1 }));
    render(<StorePage />);
    expect(await card("Clock")).toBeInTheDocument();
    await waitFor(() => expect(calls("catalog_refresh")).toHaveLength(1));
    await waitFor(() => expect(calls("catalog_list").length).toBeGreaterThanOrEqual(2));
  });

  it("an empty catalog says so", async () => {
    backend();
    catalog([]);
    render(<StorePage />);
    expect(await screen.findByText("The catalog has no screens yet.")).toBeInTheDocument();
  });

  it("a card shows picture, name, author, version; badges render; it is a Focusable with an accessible name", async () => {
    backend();
    catalog([
      ent("clock", { name: "Clock", author: "Ada", version: "1.4.0", description: "A clock" }),
      ent("got", { name: "Got", installed: true, installed_version: "1.0.0" }),
      ent("upd", { name: "Upd", installed: true, installed_version: "1.0.0", update_available: true }),
      ent("code", { name: "Code", has_code: true }),
      ent("need", { name: "Need", has_code: true, installed: true, installed_version: "1.0.0", needs_approval: true }),
      ent("old", { name: "Old", compatible: false, incompatible_reason: "needs plugin 2.0.0" }),
    ]);
    render(<StorePage />);
    const c = within(await card("Clock"));
    expect(c.getByText("by Ada · v1.4.0")).toBeInTheDocument();
    expect(await c.findByRole("img", { name: "Preview of Clock" })).toHaveAttribute("src", PNG);
    expect(within(await card("Got")).getByText("Installed")).toBeInTheDocument();
    const upd = within(await card("Upd"));
    expect(upd.getByText("Update available")).toBeInTheDocument();
    expect(upd.queryByText("Installed")).toBeNull(); // not both
    expect(within(await card("Code")).getByText("Runs code")).toBeInTheDocument();
    expect(within(await card("Need")).getByText("Needs approval")).toBeInTheDocument();
    expect(within(await card("Old")).getByText("Incompatible")).toBeInTheDocument();
    expect(calls("catalog_preview").map((a) => a[0])).toContain("clock");
  });

  it("the cards appear in catalog order", async () => {
    backend();
    catalog([ent("a", { name: "Alpha" }), ent("b", { name: "Bravo" }), ent("c", { name: "Charlie" })]);
    render(<StorePage />);
    await card("Alpha");
    const names = screen.getAllByRole("group").map((g) => g.getAttribute("aria-label"));
    expect(names).toEqual(["Alpha", "Bravo", "Charlie"]);
  });

  it("a hint bar is present", async () => {
    backend();
    catalog([ent("clock", { name: "Clock" })]);
    render(<StorePage />);
    await card("Clock");
    expect(screen.getByText("Details")).toBeInTheDocument();
    expect(screen.getByText("Refresh")).toBeInTheDocument();
    expect(screen.getByText("Back")).toBeInTheDocument();
  });

  it("a failed refresh with a cache shows the saved-time warning and keeps the list, Retry re-reads", async () => {
    backend();
    __mock.on("catalog_list", () => ({ ok: true, entries: [ent("clock", { name: "Clock" })], fetched_at: 1_700_000_000, url: "https://cat/" }));
    __mock.on("catalog_refresh", () => ({ ok: false, error: "network is unreachable" }));
    render(<StorePage />);
    expect(await card("Clock")).toBeInTheDocument();
    expect(await screen.findByRole("alert")).toHaveTextContent(/Showing the saved catalog from .*: network is unreachable/);
    __mock.on("catalog_refresh", () => ({ ok: true, fetched_at: 1_700_000_000, count: 1 }));
    fireEvent.click(screen.getByRole("button", { name: /Retry/ }));
    await waitFor(() => expect(screen.queryByText(/network is unreachable/)).toBeNull());
  });

  it("a backend that is down shows the error from catalog_list", async () => {
    backend();
    __mock.on("catalog_list", () => ({ ok: false, error: "Inkterface is not running" }));
    __mock.on("catalog_refresh", () => ({ ok: false, error: "Inkterface is not running" }));
    render(<StorePage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Inkterface is not running");
  });
});

describe("StorePage not-set-up state", () => {
  it("a key-less build shows a friendly panel, not a red alert", async () => {
    backend();
    __mock.on("catalog_list", () => ({ ok: true, entries: [], fetched_at: null, url: "https://cat/" }));
    __mock.on("catalog_refresh", () => ({ ok: false, error: "no catalog signing key is bundled in this plugin build; the screen catalog is disabled" }));
    render(<StorePage />);
    expect(await screen.findByText("The Screen Store is not set up yet")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Open Settings" }));
    expect(Navigation.Navigate).toHaveBeenCalledWith("/inkterface/settings");
  });

  it("another backend failure keeps the error line and a Retry, not the friendly state", async () => {
    backend();
    __mock.on("catalog_list", () => ({ ok: true, entries: [], fetched_at: null, url: "https://cat/" }));
    __mock.on("catalog_refresh", () => ({ ok: false, error: "network is unreachable" }));
    render(<StorePage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("network is unreachable");
    expect(screen.getByRole("button", { name: /Retry/ })).toBeInTheDocument();
    expect(screen.queryByText("The Screen Store is not set up yet")).toBeNull();
  });
});

describe("StorePage details sheet", () => {
  it("opening a card shows the sheet with the entry's facts and an Install button", async () => {
    backend();
    catalog([ent("clock", { name: "Clock", author: "Ada", version: "1.4.0", description: "A clock", size: 4096 })]);
    render(<StorePage />);
    const d = await openSheet("Clock");
    expect(d.getByText("A clock")).toBeInTheDocument();
    expect(d.getByText("Ada")).toBeInTheDocument();
    expect(d.getByText("4 KB")).toBeInTheDocument();
    expect(d.getByRole("button", { name: "Install Clock" })).toBeInTheDocument();
  });

  it("pressing A (Enter) on a focused card opens the sheet", async () => {
    backend();
    catalog([ent("clock", { name: "Clock" })]);
    render(<StorePage />);
    fireEvent.keyDown(await card("Clock"), { key: "Enter" });
    expect(await screen.findByRole("dialog")).toHaveTextContent("Clock");
  });

  it("installs a declarative screen with (id, version) and shows a note", async () => {
    backend();
    catalog([ent("clock", { name: "Clock", version: "1.4.0" })]);
    __mock.on("catalog_install", () => ({ ok: true, needs_approval: false, package: scr("clock", { name: "Clock", builtin: false }) }));
    render(<StorePage />);
    const d = await openSheet("Clock");
    fireEvent.click(d.getByRole("button", { name: "Install Clock" }));
    await waitFor(() => expect(calls("catalog_install")).toEqual([["clock", "1.4.0"]]));
    expect(await screen.findByText("Clock 1.4.0 installed.")).toBeInTheDocument();
  });

  it("reloads the list on screens_changed after an install", async () => {
    backend();
    catalog([ent("clock", { name: "Clock" })]);
    render(<StorePage />);
    await card("Clock");
    __mock.on("catalog_list", () => ({ ok: true, entries: [ent("clock", { name: "Clock", installed: true, installed_version: "1.0.0" })], fetched_at: 1_700_000_000, url: "https://cat/" }));
    act(() => __mock.emit("screens_changed"));
    await waitFor(() => expect(within(screen.getByRole("group", { name: "Clock" })).getByText("Installed")).toBeInTheDocument());
  });

  it("installing a code screen opens the approval dialog with the sha256, and Approve sends it", async () => {
    backend();
    catalog([ent("hello", { name: "Hello", version: "2.0.0", has_code: true })]);
    __mock.on("catalog_install", () => ({ ok: true, needs_approval: true, package: scr("hello", { name: "Hello", version: "2.0.0", builtin: false, has_code: true, code_sha256: SHA, approved: false, needs_approval: true }) }));
    render(<StorePage />);
    const d = await openSheet("Hello");
    fireEvent.click(d.getByRole("button", { name: "Install Hello" }));
    const approve = await screen.findByText("Approve and enable");
    const dlg = within(approve.closest('[role="dialog"]') as HTMLElement);
    expect(dlg.getByText(SHA)).toBeInTheDocument();
    expect(calls("approve_package")).toEqual([]);
    fireEvent.click(dlg.getByText("Approve and enable"));
    await waitFor(() => expect(calls("approve_package")).toEqual([["hello", "2.0.0", SHA]]));
    expect(await screen.findByText("Hello approved and enabled.")).toBeInTheDocument();
  });

  it("cancelling the approval dialog approves nothing", async () => {
    backend();
    catalog([ent("hello", { name: "Hello", has_code: true })]);
    __mock.on("catalog_install", () => ({ ok: true, needs_approval: true, package: scr("hello", { name: "Hello", builtin: false, has_code: true, code_sha256: SHA, needs_approval: true }) }));
    render(<StorePage />);
    const d = await openSheet("Hello");
    fireEvent.click(d.getByRole("button", { name: "Install Hello" }));
    const cancel = await screen.findByText("Cancel");
    fireEvent.click(cancel);
    expect(calls("approve_package")).toEqual([]);
  });

  it("an installed-but-unapproved screen offers Review and approve using list_screens' sha256", async () => {
    backend({ screens: [scr("hello", { name: "Hello", version: "2.0.0", builtin: false, has_code: true, code_sha256: SHA, approved: false, needs_approval: true, enabled: false })] });
    catalog([ent("hello", { name: "Hello", version: "2.0.0", has_code: true, installed: true, installed_version: "2.0.0", needs_approval: true })]);
    render(<StorePage />);
    const d = await openSheet("Hello");
    fireEvent.click(d.getByRole("button", { name: /Review and approve/ }));
    fireEvent.click(within(screen.getAllByRole("dialog").at(-1)!).getByText("Approve and enable"));
    await waitFor(() => expect(calls("approve_package")).toEqual([["hello", "2.0.0", SHA]]));
  });

  it("an update uses catalog_install with the catalog version", async () => {
    backend();
    catalog([ent("clock", { name: "Clock", version: "2.0.0", installed: true, installed_version: "1.0.0", update_available: true })]);
    render(<StorePage />);
    const d = await openSheet("Clock");
    fireEvent.click(d.getByRole("button", { name: "Update to 2.0.0 Clock" }));
    await waitFor(() => expect(calls("catalog_install")).toEqual([["clock", "2.0.0"]]));
  });

  it("an install error shows inside the sheet", async () => {
    backend();
    catalog([ent("clock", { name: "Clock" })]);
    __mock.on("catalog_install", () => ({ ok: false, error: "downgrade refused: a newer version is installed" }));
    render(<StorePage />);
    const d = await openSheet("Clock");
    fireEvent.click(d.getByRole("button", { name: "Install Clock" }));
    expect(await screen.findByText(/downgrade refused/)).toBeInTheDocument();
  });

  it("an incompatible screen's Install button is disabled and the reason is shown", async () => {
    backend();
    catalog([ent("old", { name: "Old", compatible: false, incompatible_reason: "needs plugin 2.0.0" })]);
    render(<StorePage />);
    const d = await openSheet("Old");
    expect(d.getByText("needs plugin 2.0.0")).toBeInTheDocument();
    expect(d.getByRole("button", { name: "Install Old" })).toBeDisabled();
  });

  it("the sheet of a code screen carries the runs-code warning", async () => {
    backend();
    catalog([ent("hello", { name: "Hello", has_code: true })]);
    render(<StorePage />);
    const d = await openSheet("Hello");
    expect(d.getByText(CODE_WARNING)).toBeInTheDocument();
  });

  it("confirms, then uninstalls from the sheet", async () => {
    backend();
    catalog([ent("clock", { name: "Clock", installed: true, installed_version: "1.0.0" })]);
    render(<StorePage />);
    const d = await openSheet("Clock");
    fireEvent.click(d.getByRole("button", { name: "Uninstall Clock" }));
    const confirm = within(screen.getAllByRole("dialog").at(-1)!);
    expect(calls("uninstall_package")).toEqual([]);
    fireEvent.click(confirm.getByText("Uninstall"));
    await waitFor(() => expect(calls("uninstall_package")).toEqual([["clock"]]));
    expect(await screen.findByText("Clock uninstalled.")).toBeInTheDocument();
  });
});

// ---- Installed tab: capsule cards like Browse, actions in the card's details sheet ---------------------------------
const declarative = scr("deco", { name: "Deco", builtin: false, author: "Ada", version: "1.2.0" });
const code = scr("hello", { name: "Hello Code", builtin: false, author: "Mallory", version: "2.3.4", has_code: true, code_sha256: SHA2, approved: false, needs_approval: true, enabled: false });
const codeOk = { ...code, approved: true, needs_approval: false, enabled: true };

const installedTab = () => fireEvent.click(screen.getByRole("tab", { name: "Installed" }));
const importing = async (path: string) => {
  fireEvent.change(await screen.findByLabelText("Path or https URL"), { target: { value: path } });
  fireEvent.click(screen.getByText("Import"));
};
const sheetOf = async (name: string) => {
  fireEvent.click(await card(name));
  return within(await screen.findByRole("dialog", { name }));
};

describe("Installed tab cards", () => {
  it("shows one card per installed package (not built-ins) with the picture, normal-case name, author and version", async () => {
    backend({ screens: [scr("telemetry"), declarative, codeOk] });
    render(<StorePage />);
    installedTab();
    const deco = within(await card("Deco"));
    expect(deco.getByText("Deco")).toBeInTheDocument();
    expect(deco.getByText("by Ada · v1.2.0")).toBeInTheDocument();
    expect(await deco.findByRole("img", { name: "Preview of Deco" })).toHaveAttribute("src", PNG);
    expect(await within(await card("Hello Code")).findByRole("img", { name: "Preview of Hello Code" })).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Telemetry" })).toBeNull();
    expect(screen.queryByText(/id deco/)).toBeNull(); // the old meta line is gone
  });

  it("badges code vs declarative", async () => {
    backend({ screens: [declarative, codeOk] });
    render(<StorePage />);
    installedTab();
    expect(within(await card("Deco")).getByText("Declarative")).toBeInTheDocument();
    expect(within(await card("Deco")).queryByText("Runs code")).toBeNull();
    expect(within(await card("Hello Code")).getByText("Runs code")).toBeInTheDocument();
  });

  it.each([
    [{ in_rotation: true }, "In Live"],
    [{ in_idle: true }, "In Standby"],
    [{ in_rotation: true, in_idle: true }, "In Live and Standby"],
    [{}, "Not used"],
  ])("shows where it is used: %o", async (o, text) => {
    backend({ screens: [{ ...declarative, ...o }] });
    render(<StorePage />);
    installedTab();
    expect(within(await card("Deco")).getByText(text)).toBeInTheDocument();
  });

  it("badges an update when the saved catalog has a newer version, and not otherwise", async () => {
    backend({ screens: [declarative, codeOk] });
    catalog([ent("deco", { name: "Deco", version: "1.3.0", installed: true, installed_version: "1.2.0", update_available: true }), ent("hello", { installed: true, installed_version: "2.3.4" })]);
    render(<StorePage />);
    installedTab();
    expect(await within(await card("Deco")).findByText("Update 1.3.0")).toBeInTheDocument();
    expect(within(await card("Hello Code")).queryByText(/^Update/)).toBeNull();
  });

  it("marks a package that waits for approval, and a disabled one", async () => {
    backend({ screens: [code, { ...declarative, enabled: false }] });
    render(<StorePage />);
    installedTab();
    expect(within(await card("Hello Code")).getByText("Needs approval")).toBeInTheDocument();
    expect(within(await card("Deco")).getByText("Disabled")).toBeInTheDocument();
  });

  it("has no full-width buttons on the cards: actions live in the sheet", async () => {
    backend({ screens: [declarative] });
    render(<StorePage />);
    installedTab();
    expect(within(await card("Deco")).queryByRole("button")).toBeNull();
  });

  it("shows when the list cannot load", async () => {
    backend();
    __mock.on("list_screens", () => { throw new Error("down"); });
    render(<StorePage />);
    installedTab();
    expect(await screen.findByRole("alert")).toHaveTextContent("Cannot load packages: down");
  });

  it("reloads on screens_changed", async () => {
    backend();
    render(<StorePage />);
    installedTab();
    await screen.findByText("No screens installed yet");
    __mock.on("list_screens", () => [declarative]);
    act(() => __mock.emit("screens_changed"));
    expect(await card("Deco")).toBeInTheDocument();
  });
});

describe("Installed tab empty state", () => {
  it("says nothing is installed and points to Browse", async () => {
    backend({ screens: [scr("telemetry")] });
    catalog([ent("clock", { name: "Clock" })]);
    render(<StorePage />);
    installedTab();
    const empty = await screen.findByRole("region", { name: "No screens installed" });
    expect(empty).toHaveTextContent("No screens installed yet");
    fireEvent.click(within(empty).getByRole("button", { name: "Browse screens" }));
    expect(screen.getByRole("tab", { name: "Browse" })).toHaveAttribute("aria-selected", "true");
    expect(await card("Clock")).toBeInTheDocument();
  });
});

describe("Installed tab developer-mode variants", () => {
  it("developer mode off: one compact line with a small Open Settings, no import form", async () => {
    backend({ screens: [declarative] });
    render(<StorePage />);
    installedTab();
    expect(await screen.findByText("Importing a .zip or a link needs Developer mode.")).toBeInTheDocument();
    expect(screen.queryByLabelText("Path or https URL")).toBeNull();
    expect(screen.queryByText("Import")).toBeNull();
    const open = screen.getByRole("button", { name: "Open Settings" });
    expect(open).toHaveClass("ink-btn-sm");
    expect(open).not.toHaveClass("ink-btn-block");
    fireEvent.click(open);
    expect(Navigation.Navigate).toHaveBeenCalledWith("/inkterface/settings");
  });

  it("developer mode on: the compact import field, no Settings pointer", async () => {
    backend({ screens: [declarative], settings: { developer_mode: true } });
    render(<StorePage />);
    installedTab();
    expect(await screen.findByRole("region", { name: "Import a screen package" })).toBeInTheDocument();
    expect(screen.getByLabelText("Path or https URL")).toBeInTheDocument();
    expect(screen.queryByText(/needs Developer mode/)).toBeNull();
    expect(screen.queryByRole("button", { name: "Open Settings" })).toBeNull();
    expect(await card("Deco")).toBeInTheDocument();
  });
});

describe("Installed tab import", () => {
  it("import is disabled for an empty source", async () => {
    backend({ settings: { developer_mode: true } });
    render(<StorePage />);
    installedTab();
    expect(await screen.findByRole("button", { name: /Import/ })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Path or https URL"), { target: { value: "   " } });
    expect(screen.getByRole("button", { name: /Import/ })).toBeDisabled();
  });

  it("installs a declarative package and sends the typed value, trimmed", async () => {
    backend({ settings: { developer_mode: true } });
    __mock.on("import_package", () => ({ ok: true, needs_approval: false, package: declarative }));
    render(<StorePage />);
    installedTab();
    await importing("  /home/deck/deco.zip ");
    expect(await screen.findByText("Deco 1.2.0 installed.")).toBeInTheDocument();
    expect(calls("import_package")).toEqual([["/home/deck/deco.zip"]]);
    expect(screen.getByLabelText("Path or https URL")).toHaveValue("");
  });

  it("shows the backend's reason when the import fails and keeps the text", async () => {
    backend({ settings: { developer_mode: true } });
    __mock.on("import_package", () => ({ ok: false, error: "http://x/y.zip: only https:// URLs are accepted" }));
    render(<StorePage />);
    installedTab();
    await importing("http://x/y.zip");
    expect(await screen.findByRole("alert")).toHaveTextContent("only https:// URLs are accepted");
    expect(screen.getByLabelText("Path or https URL")).toHaveValue("http://x/y.zip");
  });

  it("importing a code package opens the approval dialog; approving sends id, version and sha256", async () => {
    backend({ screens: [code], settings: { developer_mode: true } });
    __mock.on("import_package", () => ({ ok: true, needs_approval: true, package: code }));
    render(<StorePage />);
    installedTab();
    await importing("/home/deck/hello.zip");
    const d = within(await screen.findByRole("dialog"));
    expect(d.getByRole("alert")).toHaveTextContent(CODE_WARNING);
    expect(screen.getByRole("dialog")).toHaveTextContent(SHA2);
    fireEvent.click(d.getByText("Approve and enable"));
    await waitFor(() => expect(calls("approve_package")).toEqual([["hello", "2.3.4", SHA2]]));
  });
});

describe("Installed tab details sheet", () => {
  it("has no Done; facts, badges and Uninstall are inside", async () => {
    backend({ screens: [{ ...declarative, in_rotation: true }] });
    render(<StorePage />);
    installedTab();
    const s = await sheetOf("Deco");
    expect(s.queryByRole("button", { name: "Done" })).toBeNull();
    expect(screen.getByRole("dialog", { name: "Deco" })).toHaveTextContent("Used inLive");
    expect(screen.getByRole("dialog", { name: "Deco" })).toHaveTextContent("TypeDeclarative");
    expect(s.getByRole("button", { name: /Uninstall/ })).not.toHaveClass("ink-btn-primary");
  });

  it("Update is the primary action, gets focus, and installs the catalog version", async () => {
    backend({ screens: [declarative] });
    catalog([ent("deco", { name: "Deco", version: "1.3.0", installed: true, installed_version: "1.2.0", update_available: true })]);
    __mock.on("catalog_install", () => ({ ok: true, needs_approval: false, package: { ...declarative, version: "1.3.0" } }));
    render(<StorePage />);
    installedTab();
    await within(await card("Deco")).findByText("Update 1.3.0");
    const s = await sheetOf("Deco");
    const upd = s.getByRole("button", { name: /Update to 1.3.0/ });
    expect(upd).toHaveClass("ink-btn-primary");
    expect(document.activeElement).toBe(upd);
    fireEvent.click(upd);
    await waitFor(() => expect(calls("catalog_install")).toEqual([["deco", "1.3.0"]]));
    expect(await screen.findByText("Deco updated to 1.3.0.")).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "Deco" })).toBeNull();
  });

  it("Add to Live appends to the rotation with the current interval", async () => {
    backend({ screens: [declarative], settings: { rotation: ["telemetry", "clock"], rotation_interval_s: 45 } });
    render(<StorePage />);
    installedTab();
    const s = await sheetOf("Deco");
    const add = s.getByRole("button", { name: /Add to Live/ });
    expect(add).toHaveClass("ink-btn-primary");
    await waitFor(() => expect(calls("get_settings").length).toBeGreaterThan(1));
    fireEvent.click(add);
    await waitFor(() => expect(calls("set_rotation")).toEqual([[["telemetry", "clock", "deco"], 45]]));
  });

  it("Add to Standby appends to the standby screens; no Add to Live for a standby-only screen", async () => {
    backend({ screens: [{ ...declarative, has_active: false, has_idle: true }], settings: { idle_screens: ["clock"] } });
    render(<StorePage />);
    installedTab();
    const s = await sheetOf("Deco");
    expect(s.queryByRole("button", { name: /Add to Live/ })).toBeNull();
    await waitFor(() => expect(calls("get_settings").length).toBeGreaterThan(1));
    fireEvent.click(s.getByRole("button", { name: /Add to Standby/ }));
    await waitFor(() => expect(calls("set_settings")).toEqual([[{ idle_screens: ["clock", "deco"] }]]));
  });

  it("no Add buttons when it is already used everywhere it can be", async () => {
    backend({ screens: [{ ...declarative, has_idle: true, in_rotation: true, in_idle: true }] });
    render(<StorePage />);
    installedTab();
    const s = await sheetOf("Deco");
    expect(s.queryByRole("button", { name: /Add to/ })).toBeNull();
  });

  it("an unapproved package: Review and approve is primary; it cannot be added before approval", async () => {
    backend({ screens: [code] });
    render(<StorePage />);
    installedTab();
    const s = await sheetOf("Hello Code");
    expect(s.queryByRole("button", { name: /Add to/ })).toBeNull();
    const rev = s.getByRole("button", { name: /Review and approve/ });
    expect(rev).toHaveClass("ink-btn-primary");
    fireEvent.click(rev);
    const dialogs = await screen.findAllByRole("dialog");
    fireEvent.click(within(dialogs[dialogs.length - 1]).getByText("Approve and enable"));
    await waitFor(() => expect(calls("approve_package")).toEqual([["hello", "2.3.4", SHA2]]));
  });

  it("focus returns to the card that opened the sheet when it closes", async () => {
    backend({ screens: [declarative] });
    render(<StorePage />);
    installedTab();
    const c = await card("Deco");
    c.focus();
    fireEvent.keyDown(c, { key: "Enter" });
    const dlg = await screen.findByRole("dialog", { name: "Deco" });
    expect(dlg.contains(document.activeElement)).toBe(true);
    fireEvent.keyDown(dlg, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "Deco" })).toBeNull();
    expect(document.activeElement).toBe(c);
  });
});

describe("Installed tab uninstall", () => {
  const confirmOf = async (name: string) => {
    const s = await sheetOf(name);
    fireEvent.click(s.getByRole("button", { name: /Uninstall/ }));
    return screen.findByRole("dialog", { name: `Uninstall “${name}”?` });
  };

  it("an unused package: asks with the plain note, then uninstalls and closes the sheet", async () => {
    backend({ screens: [declarative] });
    render(<StorePage />);
    installedTab();
    const d = await confirmOf("Deco");
    expect(d).toHaveTextContent(STORE_REINSTALL_NOTE);
    expect(d).not.toHaveTextContent(/rotation|Standby screens/);
    expect(calls("uninstall_package")).toEqual([]);
    fireEvent.click(within(d).getByText("Uninstall"));
    await waitFor(() => expect(calls("uninstall_package")).toEqual([["deco"]]));
    expect(await screen.findByText("Deco uninstalled.")).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "Deco" })).toBeNull();
  });

  it.each([
    [{ in_rotation: true }, "Deco is in your Live rotation. Uninstalling takes it out of there too."],
    [{ in_idle: true }, "Deco is in your Standby screens. Uninstalling takes it out of there too."],
    [{ in_rotation: true, in_idle: true }, "Deco is in your Live rotation and your Standby screens."],
  ])("a package in use (%o) says so before it is removed", async (o, text) => {
    backend({ screens: [{ ...declarative, ...o }] });
    render(<StorePage />);
    installedTab();
    const d = await confirmOf("Deco");
    expect(d).toHaveTextContent(text);
    fireEvent.click(within(d).getByText("Uninstall"));
    await waitFor(() => expect(calls("uninstall_package")).toEqual([["deco"]]));
  });

  it("cancel keeps the package", async () => {
    backend({ screens: [{ ...declarative, in_rotation: true }] });
    render(<StorePage />);
    installedTab();
    fireEvent.click(within(await confirmOf("Deco")).getByText("Cancel"));
    expect(calls("uninstall_package")).toEqual([]);
    expect(screen.getByRole("dialog", { name: "Deco" })).toBeInTheDocument();
  });
});
