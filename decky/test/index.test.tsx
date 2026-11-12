import { render, screen } from "@testing-library/react";
import { ReactElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import plugin from "../src/index";
import { backend } from "./fixtures";
import { __mock, routerHook } from "./mocks/decky-api";

type Def = { name: string; content: ReactElement; titleView: ReactElement; icon: ReactElement; onDismount: () => void };
const load = () => (plugin as unknown as () => Def)();

function steam() {
  const unregister = vi.fn();
  let cb: (n: unknown) => void = () => undefined;
  (globalThis as { SteamClient?: unknown }).SteamClient = {
    GameSessions: { RegisterForAppLifetimeNotifications: (f: typeof cb) => ((cb = f), { unregister }) },
  };
  return { fire: (n: unknown) => cb(n), unregister };
}

afterEach(() => delete (globalThis as { SteamClient?: unknown }).SteamClient);

describe("plugin entry", () => {
  it("registers the settings route and returns the quick-access panel", async () => {
    backend();
    const def = load();
    expect(def.name).toBe("Inkterface");
    expect(routerHook.addRoute).toHaveBeenCalledWith("/inkterface", expect.any(Function), { exact: false });
    render(<>{def.titleView}{def.content}</>);
    expect(await screen.findByTestId("status-line")).toHaveTextContent("Connected");
    expect(screen.getByText("Inkterface", { selector: ".title" })).toBeInTheDocument();
  });

  it("the quick-access icon is the Inkterface pixel-art icon, drawn without smoothing", () => {
    backend();
    const def = load();
    const { container } = render(def.icon);
    const img = container.querySelector("img");
    expect(img).not.toBeNull();
    expect(img!.getAttribute("src")).toMatch(/icon.*\.png|^data:image\/png/);
    expect(img!.style.imageRendering).toBe("pixelated");
    expect(img!.getAttribute("alt")).toBe("");
  });

  it("launch -> set_now_playing(appid), exit -> null", () => {
    backend();
    const s = steam();
    load();
    s.fire({ unAppID: 570, bRunning: true });
    s.fire({ unAppID: 570, bRunning: false });
    expect(__mock.callsTo("set_now_playing")).toEqual([[570], [null]]);
  });

  it("a backend that rejects the call does not break the event handler", async () => {
    backend();
    __mock.on("set_now_playing", () => { throw new Error("restarting"); });
    const s = steam();
    load();
    expect(() => s.fire({ unAppID: 570, bRunning: true })).not.toThrow();
    await Promise.resolve();
  });

  it("dismount removes the route and the SteamClient subscription", () => {
    backend();
    const s = steam();
    const def = load();
    def.onDismount();
    expect(routerHook.removeRoute).toHaveBeenCalledWith("/inkterface");
    expect(s.unregister).toHaveBeenCalledOnce();
  });

  it("loads fine when SteamClient does not exist at all", () => {
    backend();
    const def = load();
    expect(() => def.onDismount()).not.toThrow();
  });
});
