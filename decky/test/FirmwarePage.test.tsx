import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { FirmwareStatus } from "../src/api";
import { InstallFirmwareDialog } from "../src/components/InstallFirmwareDialog";
import { FirmwarePage, NO_KEY_TITLE } from "../src/pages/FirmwarePage";
import { backend, firmware, installing } from "./fixtures";
import { __mock } from "./mocks/decky-api";
import { Navigation } from "./mocks/decky-ui";

const value = (label: string) => screen.getByRole("group", { name: label });
const ready = async (o: Partial<FirmwareStatus> = {}) => {
  backend({ firmware: o });
  render(<FirmwarePage />);
  await screen.findByRole("group", { name: "Installed" });
};
const push = (o: Partial<FirmwareStatus>) => act(() => __mock.emit("firmware", firmware(o)));
const installButton = () => screen.queryByRole("button", { name: /^Install firmware/ });

describe("FirmwarePage: loading and errors", () => {
  it("shows loading, then the page", async () => {
    backend();
    render(<FirmwarePage />);
    expect(screen.getByText("Loading…")).toBeInTheDocument();
    expect(await screen.findByRole("group", { name: "Installed" })).toBeInTheDocument();
  });

  it("says when the backend is down", async () => {
    __mock.on("get_firmware_status", () => { throw new Error("Inkterface is not running"); });
    render(<FirmwarePage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Cannot reach the backend: Inkterface is not running");
  });

  it("keeps showing the last status when a later refetch fails", async () => {
    await ready();
    __mock.on("get_firmware_status", () => { throw new Error("blip"); });
    act(() => __mock.emit("status"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Cannot reach the backend: blip");
    expect(value("Installed")).toHaveTextContent("1.0.0");
  });

  it("refetches when the panel connects or leaves", async () => {
    await ready({ installed: { version: null, dev_build: false, protocol_version: null, connected: false, seen: false } });
    expect(value("Installed")).toHaveTextContent("panel not connected");
    __mock.on("get_firmware_status", () => firmware());
    act(() => __mock.emit("status"));
    await waitFor(() => expect(value("Installed")).toHaveTextContent("1.0.0"));
  });

  it("unsubscribes on unmount", async () => {
    backend();
    const { unmount } = render(<FirmwarePage />);
    await screen.findByRole("group", { name: "Installed" });
    expect(__mock.listenerCount("firmware")).toBe(1);
    unmount();
    expect(__mock.listenerCount("firmware")).toBe(0);
    expect(__mock.listenerCount("status")).toBe(0);
  });
});

describe("FirmwarePage: versions", () => {
  it("shows installed, latest and last checked", async () => {
    await ready();
    expect(value("Installed")).toHaveTextContent("1.0.0");
    expect(value("Latest")).toHaveTextContent("1.2.0");
    expect(value("Last checked")).not.toHaveTextContent("never");
  });

  it("marks a development build and a disconnected panel", async () => {
    await ready({ installed: { version: "gabc123-DIRTY", dev_build: true, protocol_version: 1, connected: false, seen: true } });
    expect(value("Installed")).toHaveTextContent("gabc123-DIRTY (development build) (panel not connected)");
  });

  it("marks the 0.0.0-dev+<sha> string a development build, and still offers the release", async () => {
    await ready({ installed: { version: "0.0.0-dev+308c457.dirty", dev_build: true, protocol_version: 1, connected: true, seen: true } });
    expect(value("Installed")).toHaveTextContent("0.0.0-dev+308c457.dirty (development build)");
    expect(screen.getByText(/Firmware 1\.2\.0 is available/)).toBeInTheDocument();
  });

  it("says up to date when nothing is newer", async () => {
    await ready({ update_available: false, installed: { version: "1.2.0", dev_build: false, protocol_version: 1, connected: true, seen: true } });
    expect(screen.getByText("The panel firmware is up to date.")).toBeInTheDocument();
    expect(installButton()).toBeNull();
  });

  it("explains a panel that is newer than the latest release instead of saying up to date", async () => {
    await ready({ update_available: false, note: "The panel runs 2.0.0, which is newer than the latest release 1.2.0." });
    expect(screen.getByText(/newer than the latest release/)).toBeInTheDocument();
    expect(screen.queryByText("The panel firmware is up to date.")).toBeNull();
    expect(installButton()).toBeNull();
  });

  it("copes with no release published and never checked", async () => {
    await ready({ latest: null, update_available: false, check: { at: null, ok: null, error: null, error_kind: null, next_at: null, checking: false } });
    expect(value("Latest")).toHaveTextContent("no release published");
    expect(value("Last checked")).toHaveTextContent("never");
    expect(installButton()).toBeNull();
  });

  it("shows the check error and keeps a known update visible", async () => {
    await ready({ check: { at: 1, ok: false, error: "GitHub is unavailable (HTTP 503)", error_kind: "NetworkError", next_at: 2, checking: false } });
    expect(screen.getByRole("alert")).toHaveTextContent("Could not check for updates: GitHub is unavailable (HTTP 503)");
    expect(value("Latest")).toHaveTextContent("1.2.0");
    expect(installButton()).toBeEnabled();
  });

  it("says unknown when the check failed and nothing is known", async () => {
    await ready({ latest: null, update_available: false, check: { at: 1, ok: false, error: "bad signature", error_kind: "BadSignature", next_at: 2, checking: false } });
    expect(value("Latest")).toHaveTextContent("unknown");
    expect(screen.getByRole("alert")).toHaveTextContent("bad signature");
  });
});

describe("FirmwarePage: trust and compatibility", () => {
  const noKey = { trust: { state: "no_key" as const, message: "No release signing key is bundled in this plugin build." }, latest: null, update_available: false,
    check: { at: 1, ok: false, error: "No release signing key is bundled in this plugin build.", error_kind: "NoTrustKey", next_at: 2, checking: false } };

  it("shows the no-signing-key state prominently and switches updating off", async () => {
    await ready(noKey);
    const alerts = screen.getAllByRole("alert");
    expect(alerts).toHaveLength(1);   // the check error is not repeated under the banner
    expect(alerts[0]).toHaveTextContent(NO_KEY_TITLE);
    expect(alerts[0]).toHaveTextContent("No release signing key is bundled in this plugin build.");
    expect(installButton()).toBeNull();
    expect(screen.getByRole("button", { name: "Check for updates" })).toBeDisabled();
  });

  it("never offers Install without a key even if the backend claims an update", async () => {
    await ready({ ...noKey, update_available: true, latest: firmware().latest });
    expect(installButton()).toBeDisabled();
  });

  it("shows the protocol mismatch and where to fix it", async () => {
    await ready({ protocol_mismatch: true, mismatch_message: "The panel firmware speaks protocol v0; this plugin needs v1. Install the latest firmware below to fix it.",
      installed: { version: "0.1.0", dev_build: false, protocol_version: 0, connected: false, seen: true } });
    expect(screen.getByRole("alert")).toHaveTextContent("Install the latest firmware below");
    expect(installButton()).toBeEnabled();
  });
});

describe("FirmwarePage: check now", () => {
  it("asks the backend and shows the answer", async () => {
    await ready({ update_available: false, latest: null });
    __mock.on("check_firmware", () => firmware());
    fireEvent.click(screen.getByRole("button", { name: "Check for updates" }));
    expect(await screen.findByText("Firmware 1.2.0 is available")).toBeInTheDocument();
    expect(__mock.callsTo("check_firmware")).toEqual([[]]);
  });

  it("is disabled while checking", async () => {
    await ready();
    let done: (s: FirmwareStatus) => void = () => undefined;
    __mock.on("check_firmware", () => new Promise<FirmwareStatus>((r) => (done = r)));
    fireEvent.click(screen.getByRole("button", { name: "Check for updates" }));
    expect(await screen.findByRole("button", { name: "Checking…" })).toBeDisabled();
    await act(async () => done(firmware()));
    expect(screen.getByRole("button", { name: "Check for updates" })).toBeEnabled();
  });

  it("shows when the backend cannot be reached", async () => {
    await ready();
    __mock.on("check_firmware", () => { throw new Error("down"); });
    fireEvent.click(screen.getByRole("button", { name: "Check for updates" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not check for updates: down");
    expect(screen.getByRole("button", { name: "Check for updates" })).toBeEnabled();
  });

  it("is disabled while a backend check is running", async () => {
    await ready({ check: { at: 1, ok: true, error: null, error_kind: null, next_at: 2, checking: true } });
    expect(screen.getByRole("button", { name: "Checking…" })).toBeDisabled();
  });
});

describe("FirmwarePage: update available", () => {
  it("shows the changelog and a way to read the release", async () => {
    await ready();
    const sec = screen.getByRole("region", { name: "Firmware 1.2.0 is available" });
    expect(sec).toHaveTextContent("- Faster refresh");
    fireEvent.click(within(sec).getByText("View release on GitHub"));
    expect(Navigation.NavigateToExternalWeb).toHaveBeenCalledWith("https://github.com/FliesWithWind/inkterface/releases/tag/fw-v1.2.0");
  });

  it("copes with an empty changelog and no release page, and a browser that is missing", async () => {
    const l = firmware().latest!;
    await ready({ latest: { ...l, changelog: "", page_url: "" } });
    expect(screen.getByText("No changelog was published.")).toBeInTheDocument();
    expect(screen.queryByText("View release on GitHub")).toBeNull();
  });

  it("does not crash when no browser is available", async () => {
    await ready();
    Navigation.NavigateToExternalWeb.mockImplementation(() => { throw new Error("no browser"); });
    fireEvent.click(screen.getByText("View release on GitHub"));
    expect(installButton()).toBeEnabled();
  });

  it("Install asks first; Cancel does nothing", async () => {
    await ready();
    fireEvent.click(installButton()!);
    const d = await screen.findByRole("dialog", { name: "Install firmware 1.2.0?" });
    expect(d).toHaveTextContent("1.0.0 → 1.2.0");
    expect(d).toHaveTextContent("about a minute");
    expect(d).toHaveTextContent("returns to the previous version by itself");
    fireEvent.click(within(d).getByText("Cancel"));
    expect(__mock.callsTo("install_firmware")).toEqual([]);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("confirming installs exactly the version that was shown", async () => {
    await ready();
    fireEvent.click(installButton()!);
    fireEvent.click(within(await screen.findByRole("dialog")).getByText("Install"));
    await waitFor(() => expect(__mock.callsTo("install_firmware")).toEqual([["1.2.0"]]));
  });

  it("shows why the backend refused", async () => {
    await ready();
    __mock.on("install_firmware", () => ({ ok: false, error: "the panel already runs 1.2.0" }));
    fireEvent.click(installButton()!);
    fireEvent.click(within(await screen.findByRole("dialog")).getByText("Install"));
    expect(await screen.findByRole("alert")).toHaveTextContent("the panel already runs 1.2.0");
  });

  it("shows a backend that went away mid-click as an error", async () => {
    await ready();
    __mock.on("install_firmware", () => { throw new Error("gone"); });
    fireEvent.click(installButton()!);
    fireEvent.click(within(await screen.findByRole("dialog")).getByText("Install"));
    expect(await screen.findByRole("alert")).toHaveTextContent("gone");
  });

  it("cannot install with no panel seen yet", async () => {
    await ready({ installed: { version: null, dev_build: false, protocol_version: null, connected: false, seen: false } });
    expect(installButton()).toBeDisabled();
    expect(screen.getByText("Plug in the panel to install this update.")).toBeInTheDocument();
  });

  it("dialog with an unknown installed version", () => {
    render(<InstallFirmwareDialog from={null} to="1.2.0" onInstall={() => undefined} />);
    expect(screen.getByRole("dialog")).toHaveTextContent("unknown → 1.2.0");
  });
});

describe("FirmwarePage: progress", () => {
  it("downloading is indeterminate and can be cancelled", async () => {
    await ready();
    push({ install: installing("downloading", { message: "Downloading firmware 1.2.0…" }) });
    const bar = await screen.findByRole("progressbar", { name: "Firmware update" });
    expect(bar).not.toHaveAttribute("aria-valuenow");
    expect(screen.getByRole("status")).toHaveTextContent("Downloading firmware 1.2.0…");
    expect(installButton()).toBeNull();   // no second install while one runs
    expect(screen.getByRole("button", { name: "Cancel update" })).toBeEnabled();
  });

  it("sending shows live percent from events", async () => {
    await ready();
    push({ install: installing("sending", { percent: 42, done: 420, message: "Sending firmware… 42%" }) });
    expect(await screen.findByRole("progressbar")).toHaveAttribute("aria-valuenow", "42");
    expect(screen.getByRole("progressbar")).toHaveTextContent("42%");
    push({ install: installing("sending", { percent: 77, done: 770, message: "Sending firmware… 77%" }) });
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "77");
  });

  it("cancel calls the backend", async () => {
    await ready();
    push({ install: installing("sending", { percent: 10 }) });
    fireEvent.click(await screen.findByRole("button", { name: "Cancel update" }));
    await waitFor(() => expect(__mock.callsTo("cancel_firmware_install")).toEqual([[]]));
  });

  it("shows why cancel was refused", async () => {
    await ready();
    __mock.on("cancel_firmware_install", () => ({ ok: false, error: "too late to cancel" }));
    push({ install: installing("sending", { percent: 99 }) });
    fireEvent.click(await screen.findByRole("button", { name: "Cancel update" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("too late to cancel");
  });

  it("confirming: cancel is off and the page says why", async () => {
    await ready();
    push({ install: installing("confirming", { percent: 100, cancellable: false, message: "Waiting for the panel to restart and confirm the new firmware…" }) });
    expect(await screen.findByRole("button", { name: "Cancel update" })).toBeDisabled();
    expect(screen.getByText(/cannot be cancelled/)).toBeInTheDocument();
    expect(screen.getByRole("progressbar")).not.toHaveAttribute("aria-valuenow");
  });

  it("preparing shows the stage", async () => {
    await ready();
    push({ install: installing("preparing", { message: "Releasing the panel…" }) });
    expect(await screen.findByRole("status")).toHaveTextContent("Releasing the panel…");
  });

  it("an install that is already running when the page opens is shown", async () => {
    backend({ firmware: { install: installing("sending", { percent: 5 }) } });
    render(<FirmwarePage />);
    expect(await screen.findByRole("progressbar")).toHaveAttribute("aria-valuenow", "5");
  });
});

describe("FirmwarePage: outcomes", () => {
  it("success", async () => {
    await ready();
    push({ install: installing("done", { percent: 100, cancellable: false, message: "Firmware 1.2.0 is installed and confirmed." }), update_available: false,
      installed: { version: "1.2.0", dev_build: false, protocol_version: 1, connected: true, seen: true } });
    expect(await screen.findByText("Firmware 1.2.0 is installed and confirmed.")).toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).toBeNull();
    expect(value("Installed")).toHaveTextContent("1.2.0");
    expect(installButton()).toBeNull();
  });

  it("failure shows the reason and that the old firmware stays", async () => {
    await ready();
    push({ install: installing("failed", { cancellable: false, error: "panel rejected OTA: OTA_FLASH_FAIL", message: "Update failed. The panel keeps (or returns to) its previous firmware." }) });
    expect(await screen.findByRole("alert")).toHaveTextContent("Firmware update failed: panel rejected OTA: OTA_FLASH_FAIL");
    expect(screen.getByText(/previous firmware/)).toBeInTheDocument();
    expect(installButton()).toBeEnabled();   // the user can retry
  });

  it("rollback pending", async () => {
    await ready();
    push({ install: installing("failed", { cancellable: false, rollback_pending: true, error: "panel did not come back on 1.2.0",
      message: "The panel did not confirm the new firmware. It rolls back to the previous version by itself within about 3 minutes; nothing is bricked." }) });
    expect(await screen.findByText(/rolls back to the previous version by itself/)).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("panel did not come back on 1.2.0");
  });

  it("cancelled", async () => {
    await ready();
    push({ install: installing("cancelled", { cancellable: false, message: "Update cancelled. The panel keeps its current firmware." }) });
    expect(await screen.findByText("Update cancelled. The panel keeps its current firmware.")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

describe("FirmwarePage: GitHub rate limit", () => {
  const msg = "GitHub allows 60 anonymous requests per hour per network, and this network has used them up.";
  const at = Math.floor(Date.now() / 1000) + 12 * 60;

  it("says when checking works again instead of the raw error", async () => {
    await ready({ check: { ...firmware().check, ok: false, error: "GitHub rate limit hit while fetching the release list", error_kind: "RateLimited", retry_at: at, limit_message: msg } });
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Could not check for updates. GitHub allows 60 anonymous requests per hour");
    expect(alert).toHaveTextContent(/Checks work again at .+ \(in about 1[12] min\)\./);
    expect(alert).not.toHaveTextContent("while fetching");
  });

  it("is a plain note, not an error, when the cached release still answered", async () => {
    await ready({ check: { ...firmware().check, retry_at: at, limit_message: `${msg} Showing the last known release.` } });
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByText(/Showing the last known release\. Checks work again at/)).toBeInTheDocument();
  });

  it("falls back to an hour when GitHub gave no reset time", async () => {
    await ready({ check: { ...firmware().check, ok: false, error: "x", retry_at: null, limit_message: msg } });
    expect(screen.getByRole("alert")).toHaveTextContent("Try again in about an hour.");
  });
});
