// Tell the backend which game is running. Every SteamClient access is guarded: Steam updates move these APIs
// around, and a missing one must leave the plugin working (the panel just never auto-switches to Now Playing).
import { Router } from "@decky/ui";

type Unregisterable = { unregister?: () => void } | undefined;
export interface Notification {
  unAppID?: number;
  bRunning?: boolean;
}

const validId = (n: unknown): n is number => typeof n === "number" && Number.isInteger(n) && n > 0;

/** Returns a function that removes the subscription. `send` is called with an appid on launch and null on exit. */
export function installNowPlaying(
  send: (appid: number | null) => Promise<unknown>,
  steamClient: unknown = (globalThis as { SteamClient?: unknown }).SteamClient,
  router: unknown = Router,
): () => void {
  let current: number | null = null;
  const set = (id: number | null) => {
    if (id === current) return;
    current = id;
    try {
      send(id).catch(() => undefined); // the backend may be restarting; the next event fixes it
    } catch {
      /* send threw synchronously */
    }
  };

  try {
    const running = (router as { MainRunningApp?: { appid?: unknown } } | undefined)?.MainRunningApp?.appid;
    if (validId(running)) set(running); // a game that was already running when the plugin loaded
  } catch {
    /* Router shape changed */
  }

  let sub: Unregisterable;
  try {
    const sessions = (steamClient as { GameSessions?: { RegisterForAppLifetimeNotifications?: unknown } } | undefined)?.GameSessions;
    const register = sessions?.RegisterForAppLifetimeNotifications;
    if (typeof register === "function") {
      sub = register.call(sessions, (n: Notification) => {
        if (!n || !validId(n.unAppID)) return;
        if (n.bRunning) set(n.unAppID);
        else if (n.unAppID === current) set(null); // another game quitting must not blank the running one
      }) as Unregisterable;
    }
  } catch {
    /* API missing or changed */
  }

  return () => {
    try {
      sub?.unregister?.();
    } catch {
      /* already gone */
    }
  };
}

const ID64 = /^\d{17}$/;

/**
 * The SteamID64 of the account signed in on this Steam Machine, or null. Decky runs the plugin in Steam's SharedJSContext, where
 * `App.m_CurrentUser.strSteamID` is set (checked on the Steam Machine); `SteamClient.User.RegisterForCurrentUserChanges` reports the
 * same user and is the fallback. Everything is guarded: Steam moves these around, and null just means "ask the user to paste a link".
 */
export function currentSteamId(g: unknown = globalThis, timeoutMs = 1500): Promise<string | null> {
  const win = g as { App?: { m_CurrentUser?: { strSteamID?: unknown } }; SteamClient?: { User?: { RegisterForCurrentUserChanges?: unknown } } };
  const valid = (v: unknown): v is string => typeof v === "string" && ID64.test(v);
  try {
    const id = win.App?.m_CurrentUser?.strSteamID;
    if (valid(id)) return Promise.resolve(id);
  } catch {
    /* App moved */
  }
  const register = win.SteamClient?.User?.RegisterForCurrentUserChanges;
  if (typeof register !== "function") return Promise.resolve(null);
  return new Promise((resolve) => {
    let sub: Unregisterable;
    let done = false;
    const finish = (id: string | null) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      try {
        sub?.unregister?.();
      } catch {
        /* already gone */
      }
      resolve(id);
    };
    const timer = setTimeout(() => finish(null), timeoutMs);
    try {
      sub = register.call(win.SteamClient!.User, (u: { strSteamID?: unknown } | undefined) => finish(valid(u?.strSteamID) ? u!.strSteamID as string : null)) as Unregisterable;
      if (done) sub?.unregister?.(); // the callback fired before register() returned
    } catch {
      finish(null);
    }
  });
}
