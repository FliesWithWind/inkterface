// Stand-in for @decky/api: callables resolve to whatever the test registered, events are fired by the test.
import { vi } from "vitest";

type Fn = (...args: any[]) => unknown;

const handlers = new Map<string, Fn>();
const listeners = new Map<string, Set<Fn>>();
const calls: [string, unknown[]][] = [];

export const __mock = {
  handlers,
  calls,
  /** Register the backend implementation of a callable. */
  on(name: string, fn: Fn) {
    handlers.set(name, fn);
    return fn;
  },
  callsTo: (name: string) => calls.filter(([n]) => n === name).map(([, a]) => a),
  emit(event: string, ...args: unknown[]) {
    listeners.get(event)?.forEach((cb) => cb(...args));
  },
  listenerCount: (event: string) => listeners.get(event)?.size ?? 0,
  reset() {
    handlers.clear();
    listeners.clear();
    calls.length = 0;
    routerHook.addRoute.mockClear();
    routerHook.removeRoute.mockClear();
  },
};

export const callable = (name: string) => (...args: unknown[]) => {
  calls.push([name, args]);
  const h = handlers.get(name);
  if (!h) return Promise.reject(new Error(`no backend handler for ${name}`));
  try {
    return Promise.resolve(h(...args));
  } catch (e) {
    return Promise.reject(e);
  }
};

export const addEventListener = (event: string, cb: Fn) => {
  if (!listeners.has(event)) listeners.set(event, new Set());
  listeners.get(event)!.add(cb);
  return cb;
};
export const removeEventListener = (event: string, cb: Fn) => void listeners.get(event)?.delete(cb);

export const routerHook = { addRoute: vi.fn(), removeRoute: vi.fn() };
export const definePlugin = (fn: unknown) => fn;
