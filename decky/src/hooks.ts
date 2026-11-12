import { DependencyList, useCallback, useEffect, useRef, useState } from "react";
import { EventName, FirmwareStatus, Status, errText, getFirmwareStatus, getStatus, onEvent, previewScreen } from "./api";

export interface Loaded<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  reload: () => void;
}

/** Run `load` now and whenever one of `events` fires (or reload() is called). Errors become `error`, never throws. */
export function useLoad<T>(load: () => Promise<T>, events: EventName[] = [], deps: DependencyList = []): Loaded<T> {
  const [state, setState] = useState<{ data: T | null; error: string | null; loading: boolean }>({ data: null, error: null, loading: true });
  const alive = useRef(true);
  const loader = useRef(load);
  loader.current = load;
  const reload = useCallback(() => {
    loader.current().then(
      (data) => alive.current && setState({ data, error: null, loading: false }),
      (e) => alive.current && setState((s) => ({ data: s.data, error: errText(e), loading: false })),
    );
  }, []);
  useEffect(() => {
    alive.current = true;
    reload();
    const offs = events.map((e) => onEvent(e, reload));
    return () => {
      alive.current = false;
      offs.forEach((off) => off());
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reload, ...deps]);
  return { ...state, reload };
}

/** Live backend status: fetched once, then pushed by the `status` event. */
export function useStatus(): { status: Status | null; error: string | null } {
  const [status, setStatus] = useState<Status | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    const off = onEvent<[Status]>("status", (s) => {
      if (alive) {
        setStatus(s);
        setError(null);
      }
    });
    getStatus().then(
      (s) => alive && (setStatus((cur) => cur ?? s), setError(null)),
      (e) => alive && setError(errText(e)),
    );
    return () => {
      alive = false;
      off();
    };
  }, []);
  return { status, error };
}

/** Firmware update state: fetched once, pushed by the `firmware` event (progress), refetched when the panel connects or leaves. */
export function useFirmware(): { fw: FirmwareStatus | null; error: string | null; set: (s: FirmwareStatus) => void } {
  const [fw, setFw] = useState<FirmwareStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    const load = () =>
      getFirmwareStatus().then(
        (s) => alive && (setFw(s), setError(null)),
        (e) => alive && setError(errText(e)),
      );
    const offFw = onEvent<[FirmwareStatus]>("firmware", (s) => alive && (setFw(s), setError(null)));
    const offStatus = onEvent("status", load);
    load();
    return () => {
      alive = false;
      offFw();
      offStatus();
    };
  }, []);
  return { fw, error, set: setFw };
}

type Rendered = { ok: true; data_url: string } | { ok: false; error: string };

/** PNG data URL of a screen rendered by the backend (`render` defaults to its active look); re-rendered when `refreshKey` changes. The old picture stays while a new one renders, but never for another screen. */
export function usePreview(screenId: string | null, refreshKey: unknown, render?: (id: string) => Promise<Rendered>): { url: string | null; error: string | null } {
  const [state, setState] = useState<{ id: string | null; url: string | null; error: string | null }>({ id: null, url: null, error: null });
  const renderer = useRef(render ?? previewScreen);
  renderer.current = render ?? previewScreen;
  useEffect(() => {
    if (!screenId) {
      setState({ id: null, url: null, error: null });
      return;
    }
    let alive = true;
    renderer.current(screenId).then((r) => {
      if (alive) setState(r.ok ? { id: screenId, url: r.data_url, error: null } : { id: screenId, url: null, error: r.error });
    });
    return () => {
      alive = false;
    };
  }, [screenId, refreshKey]);
  return state.id === screenId ? { url: state.url, error: state.error } : { url: null, error: null };
}

/** Wraps a mutation: tracks busy and the last error string. */
export function useAction(): { busy: boolean; error: string | null; run: <R extends { ok: boolean }>(p: Promise<R>) => Promise<R>; clear: () => void } {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  const run = useCallback(async <R extends { ok: boolean }>(p: Promise<R>): Promise<R> => {
    setBusy(true);
    setError(null);
    const r = await p;
    if (alive.current) {
      setBusy(false);
      if (!r.ok) setError((r as unknown as { error: string }).error);
    }
    return r;
  }, []);
  return { busy, error, run, clear: () => setError(null) };
}
