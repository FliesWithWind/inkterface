import { describe, expect, it, vi } from "vitest";
import * as api from "../src/api";
import { __mock } from "./mocks/decky-api";

describe("mutation wrappers", () => {
  it("pass arguments to the callable and return its result", async () => {
    __mock.on("pin_screen", () => ({ ok: true }));
    expect(await api.pinScreen("telemetry")).toEqual({ ok: true });
    expect(__mock.callsTo("pin_screen")).toEqual([["telemetry"]]);
  });

  it("turn a rejected call into { ok: false, error }", async () => {
    __mock.on("pin_screen", () => { throw new Error("Inkterface is not running"); });
    expect(await api.pinScreen("x")).toEqual({ ok: false, error: "Inkterface is not running" });
  });

  it("stringify non-Error rejections", async () => {
    __mock.on("unpin_screen", () => Promise.reject("plain string"));
    expect(await api.unpinScreen()).toEqual({ ok: false, error: "plain string" });
  });

  it.each([undefined, null, "ok", 5])("reject a malformed reply %j", async (reply) => {
    __mock.on("set_rotation", () => reply);
    const r = await api.setRotation(["a"], null);
    expect(r).toEqual({ ok: false, error: "unexpected reply from the backend" });
  });

  it("keeps the backend's error text", async () => {
    __mock.on("import_package", () => ({ ok: false, error: "not a zip" }));
    expect(await api.importPackage("/x")).toEqual({ ok: false, error: "not a zip" });
  });

  it.each([
    ["setSettings", () => api.setSettings({ units: "metric" }), "set_settings"],
    ["setScreenSettings", () => api.setScreenSettings("a", {}), "set_screen_settings"],
    ["setScreenEnabled", () => api.setScreenEnabled("a", true), "set_screen_enabled"],
    ["setNowPlaying", () => api.setNowPlaying(5), "set_now_playing"],
    ["approvePackage", () => api.approvePackage("a", "1.0.0", "ff"), "approve_package"],
    ["uninstallPackage", () => api.uninstallPackage("a"), "uninstall_package"],
    ["previewScreen", () => api.previewScreen("a"), "preview_screen"],
    ["previewIdleScreen", () => api.previewIdleScreen("a", "gray4"), "preview_idle_screen"],
    ["pushIdleLayouts", () => api.pushIdleLayouts(), "push_idle_layouts"],
    ["installFirmware", () => api.installFirmware("1.2.0"), "install_firmware"],
    ["cancelFirmwareInstall", () => api.cancelFirmwareInstall(), "cancel_firmware_install"],
    ["catalogRefresh", () => api.catalogRefresh(), "catalog_refresh"],
    ["catalogList", () => api.catalogList(), "catalog_list"],
    ["catalogUpdates", () => api.catalogUpdates(), "catalog_updates"],
    ["catalogInstall", () => api.catalogInstall("id", "1.0.0"), "catalog_install"],
    ["catalogPreview", () => api.catalogPreview("id"), "catalog_preview"],
  ])("%s calls %s", async (_n, call, name) => {
    __mock.on(name, () => ({ ok: true }));
    expect((await call()).ok).toBe(true);
    expect(__mock.callsTo(name)).toHaveLength(1);
  });
});

describe("reads", () => {
  it("firmware status and check are plain reads that reject when the backend does", async () => {
    await expect(api.getFirmwareStatus()).rejects.toThrow("no backend handler");
    __mock.on("check_firmware", () => { throw new Error("down"); });
    await expect(api.checkFirmware()).rejects.toThrow("down");
  });

  it("install_firmware passes the version through", async () => {
    __mock.on("install_firmware", (v: string) => ({ ok: true, v }));
    await api.installFirmware("9.9.9");
    expect(__mock.callsTo("install_firmware")).toEqual([["9.9.9"]]);
  });

  it("reject when the backend does (hooks show that as an error state)", async () => {
    await expect(api.getStatus()).rejects.toThrow("no backend handler");
  });
});

describe("onEvent", () => {
  it("delivers events and unsubscribes", () => {
    const cb = vi.fn();
    const off = api.onEvent("status", cb);
    __mock.emit("status", { a: 1 });
    expect(cb).toHaveBeenCalledWith({ a: 1 });
    off();
    __mock.emit("status", { a: 2 });
    expect(cb).toHaveBeenCalledOnce();
    expect(__mock.listenerCount("status")).toBe(0);
  });
});

describe("errText", () => {
  it("handles Errors and other values", () => {
    expect(api.errText(new Error("x"))).toBe("x");
    expect(api.errText(5)).toBe("5");
  });
});

describe("catalog wrappers", () => {
  it("catalogList never rejects: a dead backend becomes { ok: false }", async () => {
    expect(await api.catalogList()).toMatchObject({ ok: false });
  });
  it("catalogInstall passes id and version and returns the backend reply", async () => {
    __mock.on("catalog_install", (id: string, v: string) => ({ ok: true, needs_approval: false, package: { id, version: v } }));
    expect(await api.catalogInstall("clock", "2.0.0")).toMatchObject({ ok: true, needs_approval: false });
    expect(__mock.callsTo("catalog_install")).toEqual([["clock", "2.0.0"]]);
  });
  it("catalogPreview keeps the backend's error", async () => {
    __mock.on("catalog_preview", () => ({ ok: false, error: "no preview" }));
    expect(await api.catalogPreview("clock")).toEqual({ ok: false, error: "no preview" });
  });
});

describe("previewIdleScreen", () => {
  it("passes the id and the mode (null = the user's choice)", async () => {
    __mock.on("preview_idle_screen", () => ({ ok: true, data_url: "data:image/png;base64,AA" }));
    expect(await api.previewIdleScreen("a", "mono")).toEqual({ ok: true, data_url: "data:image/png;base64,AA" });
    await api.previewIdleScreen("a", null);
    expect(__mock.callsTo("preview_idle_screen")).toEqual([["a", "mono"], ["a", null]]);
  });
  it("a missing backend is { ok: false, error }, never a rejection", async () => {
    expect(await api.previewIdleScreen("a", "mono")).toMatchObject({ ok: false });
  });
});
