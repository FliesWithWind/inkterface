import { describe, expect, it, vi } from "vitest";
import { currentSteamId, installNowPlaying } from "../src/steam";
import { Router } from "./mocks/decky-ui";

type Cb = (n: unknown) => void;

function fakeSteam() {
  const unregister = vi.fn();
  let cb: Cb = () => undefined;
  const register = vi.fn(function (this: unknown, f: Cb) {
    cb = f;
    return { unregister };
  });
  return { client: { GameSessions: { RegisterForAppLifetimeNotifications: register } }, fire: (n: unknown) => cb(n), register, unregister };
}

const setup = (steam: unknown = fakeSteam().client, router: unknown = {}) => {
  const send = vi.fn(() => Promise.resolve({ ok: true }));
  const off = installNowPlaying(send, steam, router);
  return { send, off };
};

describe("now playing from SteamClient", () => {
  it("launch sends the appid, exit sends null", () => {
    const s = fakeSteam();
    const { send } = setup(s.client);
    s.fire({ unAppID: 570, nInstanceID: 1, bRunning: true });
    s.fire({ unAppID: 570, nInstanceID: 1, bRunning: false });
    expect(send.mock.calls).toEqual([[570], [null]]);
  });

  it("registers on GameSessions itself (keeps `this`)", () => {
    const s = fakeSteam();
    setup(s.client);
    expect(s.register.mock.contexts[0]).toBe(s.client.GameSessions);
  });

  it("ignores repeated events and the exit of a game that is not the current one", () => {
    const s = fakeSteam();
    const { send } = setup(s.client);
    s.fire({ unAppID: 570, bRunning: true });
    s.fire({ unAppID: 570, bRunning: true });
    s.fire({ unAppID: 730, bRunning: false });
    expect(send.mock.calls).toEqual([[570]]);
    s.fire({ unAppID: 730, bRunning: true });
    s.fire({ unAppID: 570, bRunning: false }); // 570 quits while 730 runs
    expect(send.mock.calls).toEqual([[570], [730]]);
  });

  it("an exit with nothing running sends nothing", () => {
    const s = fakeSteam();
    const { send } = setup(s.client);
    s.fire({ unAppID: 570, bRunning: false });
    expect(send).not.toHaveBeenCalled();
  });

  it.each([undefined, null, {}, { unAppID: 0, bRunning: true }, { unAppID: -5, bRunning: true }, { unAppID: 1.5, bRunning: true },
    { unAppID: "570", bRunning: true }, { unAppID: NaN, bRunning: true }])("ignores malformed notification %j", (n) => {
    const s = fakeSteam();
    const { send } = setup(s.client);
    s.fire(n);
    expect(send).not.toHaveBeenCalled();
  });

  it("accepts the large appids of non-Steam shortcuts", () => {
    const s = fakeSteam();
    const { send } = setup(s.client);
    s.fire({ unAppID: 2147483649, bRunning: true });
    expect(send).toHaveBeenCalledWith(2147483649);
  });

  it("a game already running at load is reported", () => {
    const { send } = setup(fakeSteam().client, { MainRunningApp: { appid: 440 } });
    expect(send).toHaveBeenCalledWith(440);
  });

  it("uses the real Router export by default", () => {
    Router.MainRunningApp = { appid: 620 };
    const send = vi.fn(() => Promise.resolve());
    installNowPlaying(send, undefined);
    expect(send).toHaveBeenCalledWith(620);
  });

  it.each([undefined, {}, { MainRunningApp: undefined }, { MainRunningApp: { appid: 0 } }, { MainRunningApp: { appid: "x" } }])(
    "no usable running app (%j) sends nothing", (router) => {
      expect(setup(fakeSteam().client, router).send).not.toHaveBeenCalled();
    });

  it("a Router that throws does not break setup", () => {
    const router = { get MainRunningApp(): never { throw new Error("shape changed"); } };
    const s = fakeSteam();
    const { send } = setup(s.client, router);
    s.fire({ unAppID: 1, bRunning: true });
    expect(send).toHaveBeenCalledWith(1);
  });

  describe("missing or broken SteamClient APIs degrade to doing nothing", () => {
    it.each([
      ["no SteamClient", undefined],
      ["null", null],
      ["no GameSessions", {}],
      ["GameSessions without the method", { GameSessions: {} }],
      ["method is not a function", { GameSessions: { RegisterForAppLifetimeNotifications: 5 } }],
      ["register throws", { GameSessions: { RegisterForAppLifetimeNotifications: () => { throw new Error("boom"); } } }],
      ["register returns nothing", { GameSessions: { RegisterForAppLifetimeNotifications: () => undefined } }],
      ["register returns no unregister", { GameSessions: { RegisterForAppLifetimeNotifications: () => ({}) } }],
    ])("%s", (_name, client) => {
      const { send, off } = setup(client);
      expect(send).not.toHaveBeenCalled();
      expect(() => off()).not.toThrow();
    });

    it("defaults to globalThis.SteamClient", () => {
      const s = fakeSteam();
      (globalThis as { SteamClient?: unknown }).SteamClient = s.client;
      try {
        const off = installNowPlaying(vi.fn(() => Promise.resolve()));
        expect(s.register).toHaveBeenCalledOnce();
        off();
      } finally {
        delete (globalThis as { SteamClient?: unknown }).SteamClient;
      }
    });
  });

  it("cleanup unregisters, and survives an unregister that throws", () => {
    const s = fakeSteam();
    const { off } = setup(s.client);
    off();
    expect(s.unregister).toHaveBeenCalledOnce();
    s.unregister.mockImplementation(() => { throw new Error("gone"); });
    expect(() => off()).not.toThrow();
  });

  it("a rejected or throwing send never escapes", async () => {
    const s = fakeSteam();
    const rejecting = vi.fn(() => Promise.reject(new Error("backend restarting")));
    installNowPlaying(rejecting, s.client, {});
    expect(() => s.fire({ unAppID: 1, bRunning: true })).not.toThrow();
    const throwing = vi.fn(() => { throw new Error("sync"); });
    const s2 = fakeSteam();
    installNowPlaying(throwing as never, s2.client, {});
    expect(() => s2.fire({ unAppID: 2, bRunning: true })).not.toThrow();
    await Promise.resolve();
  });
});

describe("currentSteamId", () => {
  const ID = "76561198012345678";

  it("reads App.m_CurrentUser.strSteamID, the way SharedJSContext exposes it", async () => {
    expect(await currentSteamId({ App: { m_CurrentUser: { strSteamID: ID } } })).toBe(ID);
  });

  it.each([undefined, null, "", "123", "7656119801234567x", 76561198012345678, "765611980123456789"])("an unusable App value %o is ignored", async (v) => {
    expect(await currentSteamId({ App: { m_CurrentUser: { strSteamID: v } } }, 20)).toBeNull();
  });

  it("nothing there at all: null, no throw", async () => {
    expect(await currentSteamId({}, 20)).toBeNull();
    expect(await currentSteamId(undefined, 20)).toBeNull();
    expect(await currentSteamId({ App: null, SteamClient: { User: {} } }, 20)).toBeNull();
  });

  it("App getter that throws falls through to SteamClient", async () => {
    const win = { get App(): never { throw new Error("moved"); }, SteamClient: { User: { RegisterForCurrentUserChanges: (cb: (u: unknown) => void) => { cb({ strSteamID: ID }); return { unregister: vi.fn() }; } } } };
    expect(await currentSteamId(win)).toBe(ID);
  });

  it("falls back to SteamClient.User, keeps `this`, and unregisters", async () => {
    const unregister = vi.fn();
    const User = { RegisterForCurrentUserChanges: vi.fn(function (this: unknown, cb: (u: unknown) => void) { setTimeout(() => cb({ strSteamID: ID }), 0); return { unregister }; }) };
    expect(await currentSteamId({ SteamClient: { User } })).toBe(ID);
    expect(User.RegisterForCurrentUserChanges.mock.contexts[0]).toBe(User);
    expect(unregister).toHaveBeenCalledOnce();
  });

  it("a callback that fires before register() returns still unregisters", async () => {
    const unregister = vi.fn();
    const win = { SteamClient: { User: { RegisterForCurrentUserChanges: (cb: (u: unknown) => void) => { cb({ strSteamID: ID }); return { unregister }; } } } };
    expect(await currentSteamId(win)).toBe(ID);
    expect(unregister).toHaveBeenCalledOnce();
  });

  it("a user without a valid id, a register that throws, and a callback that never comes all give null", async () => {
    const mk = (RegisterForCurrentUserChanges: unknown) => ({ SteamClient: { User: { RegisterForCurrentUserChanges } } });
    expect(await currentSteamId(mk((cb: (u: unknown) => void) => { setTimeout(() => cb({ strSteamID: "1" }), 0); return {}; }), 200)).toBeNull();
    expect(await currentSteamId(mk(() => { throw new Error("boom"); }), 200)).toBeNull();
    const unregister = vi.fn();
    expect(await currentSteamId(mk(() => ({ unregister })), 20)).toBeNull();
    expect(unregister).toHaveBeenCalledOnce();
  });

  it("the first answer wins", async () => {
    const win = { SteamClient: { User: { RegisterForCurrentUserChanges: (cb: (u: unknown) => void) => { setTimeout(() => { cb({ strSteamID: ID }); cb({ strSteamID: "76561198099999999" }); }, 0); return { unregister: vi.fn() }; } } } };
    expect(await currentSteamId(win)).toBe(ID);
  });
});
