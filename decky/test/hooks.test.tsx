import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useAction, useLoad, usePreview } from "../src/hooks";
import { __mock } from "./mocks/decky-api";

describe("useLoad", () => {
  it("loads, exposes errors and reloads on demand", async () => {
    let n = 0;
    const load = vi.fn(async () => {
      if (++n === 2) throw new Error("boom");
      return n;
    });
    const { result } = renderHook(() => useLoad(load));
    expect(result.current.loading).toBe(true);
    await waitFor(() => expect(result.current.data).toBe(1));
    act(() => result.current.reload());
    await waitFor(() => expect(result.current.error).toBe("boom"));
    expect(result.current.data).toBe(1); // keeps the last good data
    act(() => result.current.reload());
    await waitFor(() => expect(result.current.data).toBe(3));
    expect(result.current.error).toBeNull();
  });

  it("does not update after unmount and unsubscribes", async () => {
    let resolve: (v: number) => void = () => undefined;
    const load = () => new Promise<number>((r) => (resolve = r));
    const { result, unmount } = renderHook(() => useLoad(load, ["screens_changed"]));
    expect(__mock.listenerCount("screens_changed")).toBe(1);
    unmount();
    expect(__mock.listenerCount("screens_changed")).toBe(0);
    resolve(5);
    await Promise.resolve();
    expect(result.current.data).toBeNull();
  });

  it("a rejection after unmount is ignored", async () => {
    let reject: (e: Error) => void = () => undefined;
    const { unmount } = renderHook(() => useLoad(() => new Promise<number>((_, r) => (reject = r))));
    unmount();
    reject(new Error("late"));
    await Promise.resolve();
  });

  it("reloads when an event fires", async () => {
    const load = vi.fn(async () => 1);
    renderHook(() => useLoad(load, ["settings_changed"]));
    await waitFor(() => expect(load).toHaveBeenCalledTimes(1));
    act(() => __mock.emit("settings_changed"));
    await waitFor(() => expect(load).toHaveBeenCalledTimes(2));
  });
});

describe("useAction", () => {
  it("tracks busy and error, clears the error on the next run", async () => {
    const { result } = renderHook(() => useAction());
    let done: (v: { ok: boolean; error?: string }) => void = () => undefined;
    let p: Promise<unknown> = Promise.resolve();
    act(() => { p = result.current.run(new Promise<{ ok: boolean; error?: string }>((r) => (done = r))); });
    expect(result.current.busy).toBe(true);
    await act(async () => { done({ ok: false, error: "nope" }); await p; });
    expect(result.current).toMatchObject({ busy: false, error: "nope" });
    await act(async () => { await result.current.run(Promise.resolve({ ok: true })); });
    expect(result.current.error).toBeNull();
    await act(async () => { await result.current.run(Promise.resolve({ ok: false, error: "x" })); });
    act(() => result.current.clear());
    expect(result.current.error).toBeNull();
  });

  it("does not set state after unmount", async () => {
    const { result, unmount } = renderHook(() => useAction());
    let done: (v: { ok: boolean }) => void = () => undefined;
    const run = result.current.run(new Promise<{ ok: boolean }>((r) => (done = r)));
    unmount();
    done({ ok: true });
    await expect(run).resolves.toEqual({ ok: true });
  });
});

describe("usePreview", () => {
  it("renders the active look by default", async () => {
    __mock.on("preview_screen", () => ({ ok: true, data_url: "data:image/png;base64,A" }));
    const { result } = renderHook(() => usePreview("a", "k"));
    await waitFor(() => expect(result.current.url).toBe("data:image/png;base64,A"));
  });

  it("uses the renderer it is given, and the latest one when the key changes", async () => {
    const first = vi.fn(async (id: string) => ({ ok: true as const, data_url: `1-${id}` }));
    const second = vi.fn(async (id: string) => ({ ok: true as const, data_url: `2-${id}` }));
    const { result, rerender } = renderHook(({ r, k }) => usePreview("a", k, r), { initialProps: { r: first, k: "mono" } });
    await waitFor(() => expect(result.current.url).toBe("1-a"));
    rerender({ r: second, k: "gray4" });
    await waitFor(() => expect(result.current.url).toBe("2-a"));
    expect(__mock.callsTo("preview_screen")).toEqual([]);
  });

  it("reports the renderer's error and nothing for a null id", async () => {
    const { result, rerender } = renderHook(({ id }) => usePreview(id, "k", async () => ({ ok: false as const, error: "no renderer" })), { initialProps: { id: "a" as string | null } });
    await waitFor(() => expect(result.current.error).toBe("no renderer"));
    rerender({ id: null });
    await waitFor(() => expect(result.current).toEqual({ url: null, error: null }));
  });
});
