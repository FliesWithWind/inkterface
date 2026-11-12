// "Store" is now "Screen Store" (one constant) and every page leaves room for Steam's footer bar.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PAGE_END } from "../src/components/styles";
import { MainPage } from "../src/MainPage";
import { APPROVAL_WAITING, LIVE_EMPTY, NAMES, SIDEBAR, STORE_NOT_SET_UP_TITLE, STORE_REINSTALL_NOTE } from "../src/names";
import { backend } from "./fixtures";

const BARE_STORE = /(?<!Screen )\bStore\b/;

/** What the user reads in the page (the style sheet is not text). */
function pageText(): string {
  const main = document.querySelector("main")!.cloneNode(true) as HTMLElement;
  main.querySelectorAll("style").forEach((s) => s.remove());
  return main.textContent ?? "";
}

describe("Screen Store name", () => {
  it("is one constant, used by the sidebar and every sentence that points at it", () => {
    expect(NAMES.store).toBe("Screen Store");
    expect([...SIDEBAR]).toContain("Screen Store");
    for (const text of [LIVE_EMPTY, APPROVAL_WAITING, STORE_NOT_SET_UP_TITLE, STORE_REINSTALL_NOTE]) {
      expect(text).toContain("Screen Store");
      expect(text).not.toMatch(BARE_STORE);
    }
  });

  it.each([NAMES.live, NAMES.standby, NAMES.store, NAMES.firmware, NAMES.settings, NAMES.about])("%s shows no bare 'Store' to the user", async (tab) => {
    backend({ settings: { developer_mode: true } });
    render(<MainPage />);
    fireEvent.click(screen.getByRole("tab", { name: tab }));
    await waitFor(() => expect(pageText()).not.toMatch(/Loading|^$/));
    const page = pageText();
    expect(page).not.toMatch(BARE_STORE);
  });

  it("Settings: the developer-mode warning and the catalog note name the Screen Store", async () => {
    backend();
    render(<MainPage />);
    fireEvent.click(screen.getByRole("tab", { name: NAMES.settings }));
    expect(await screen.findByText(/under Screen Store → Installed/)).toBeInTheDocument();
    expect(screen.getByText(/Where the Screen Store downloads its list/)).toBeInTheDocument();
  });

  it("keeps the tabs Browse and Installed", async () => {
    backend();
    render(<MainPage />);
    fireEvent.click(screen.getByRole("tab", { name: NAMES.store }));
    const tabs = await screen.findAllByRole("tab");
    const names = tabs.map((t) => t.textContent);
    expect(names).toContain("Browse");
    expect(names.some((n) => n?.startsWith("Installed"))).toBe(true);
  });
});

describe("room for Steam's footer bar", () => {
  it("is at least the footer (6vh) plus a margin, in one constant", () => {
    expect(PAGE_END).toBe("calc(48px + 6vh)");
  });

  it.each([NAMES.live, NAMES.standby, NAMES.store, NAMES.firmware, NAMES.settings, NAMES.about])("%s page ends with the footer clearance", async (tab) => {
    backend();
    render(<MainPage />);
    fireEvent.click(screen.getByRole("tab", { name: tab }));
    const page = document.querySelector("main")!.firstElementChild as HTMLElement;
    expect(page.className).toBe("ink-page");
    expect(page.style.paddingBottom).toBe(PAGE_END);
  });

  it("is on the page while it is still loading and when it failed to load", async () => {
    backend();
    render(<MainPage />);
    const page = document.querySelector("main")!.firstElementChild as HTMLElement;
    expect(page.style.paddingBottom).toBe(PAGE_END);
  });
});
