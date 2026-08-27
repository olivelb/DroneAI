import { afterEach, describe, expect, it, vi } from "vitest";
import { GsTileRangeScheduler } from "./range-source";

const range = { start: 0, length: 4 };
const response = () => new Response(new Uint8Array([1, 2, 3, 4]), {
  status: 206, headers: { "Content-Range": "bytes 0-3/4" },
});
const deferred = <T>() => {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
};
const cacheWithRead = (read: (key: string) => Promise<ArrayBuffer | null>) => ({
  read: vi.fn(read),
  write: vi.fn(async () => undefined),
  delete: vi.fn(async () => undefined),
});

describe("GSTile cache and network isolation", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("retains recently demanded tiles through a speculative scan at the same byte cap", async () => {
    const network = vi.fn(async () => response());
    vi.stubGlobal("fetch", network);
    const scheduler = new GsTileRangeScheduler(1, 16);
    for (const id of ["door", "facade", "detail"]) {
      await scheduler.fetch(id, range);
    }
    for (const id of ["prediction-1", "prediction-2", "prediction-3", "prediction-4"]) {
      await scheduler.fetch(id, range, undefined, undefined, undefined, "prefetch");
      expect(scheduler.statistics().cacheBytes).toBeLessThanOrEqual(16);
    }
    for (const id of ["door", "facade", "detail"]) {
      await scheduler.fetch(id, range);
    }
    expect(network).toHaveBeenCalledTimes(7);
    expect(scheduler.statistics()).toMatchObject({ cacheHits: 3, cacheBytes: 16 });
  });

  it("allows a cached tile to complete while the only network slot is occupied", async () => {
    const gate = deferred<Response>();
    const network = vi.fn(() => gate.promise);
    vi.stubGlobal("fetch", network);
    const cached = new Uint8Array([1, 2, 3, 4]).buffer;
    const persistent = cacheWithRead(async () => cached);
    const scheduler = new GsTileRangeScheduler(1, 16, 0, persistent);
    const download = scheduler.fetch("network-only", range);
    await vi.waitFor(() => expect(network).toHaveBeenCalledOnce());
    const disk = scheduler.fetch("disk", range, undefined, "disk-id");
    try {
      await vi.waitFor(() => expect(persistent.read).toHaveBeenCalledOnce());
      expect(await disk).toBe(cached);
      expect(scheduler.statistics()).toMatchObject({ active: 1, persistentCacheHits: 1 });
    } finally {
      gate.resolve(response());
      await Promise.all([download, disk]);
    }
  });

  it("allows network work to complete while persistent storage is busy", async () => {
    const gate = deferred<ArrayBuffer | null>();
    const persistent = cacheWithRead(() => gate.promise);
    const network = vi.fn(async () => response());
    vi.stubGlobal("fetch", network);
    const scheduler = new GsTileRangeScheduler(1, 16, 0, persistent);
    const disk = scheduler.fetch("disk", range, undefined, "disk-id");
    await vi.waitFor(() => expect(persistent.read).toHaveBeenCalledOnce());
    const download = scheduler.fetch("network-only", range);
    try {
      await vi.waitFor(() => expect(network).toHaveBeenCalledOnce());
      expect(new Uint8Array(await download)).toEqual(new Uint8Array([1, 2, 3, 4]));
    } finally {
      gate.resolve(new ArrayBuffer(4));
      await Promise.all([disk, download]);
    }
  });

  it.each([0, -1, 1.5, Infinity])("rejects invalid persistent concurrency %s", (concurrency) => {
    expect(() => new GsTileRangeScheduler(1, 16, 0, null, concurrency)).toThrow(/persistent read concurrency/);
  });

  it("bounds disk reads separately and promotes a queued prediction on demand", async () => {
    const gate = deferred<ArrayBuffer | null>();
    const persistent = cacheWithRead(async (key) => key.includes("active") ? gate.promise : new ArrayBuffer(4));
    const network = vi.fn(async () => response());
    vi.stubGlobal("fetch", network);
    const scheduler = new GsTileRangeScheduler(3, 32, 0, persistent, 1);
    const active = scheduler.fetch("active", range, undefined, "active");
    await vi.waitFor(() => expect(persistent.read).toHaveBeenCalledOnce());
    const unrelated = scheduler.fetch("unrelated", range, undefined, "unrelated", undefined, "prefetch");
    const prediction = scheduler.fetch("prediction", range, undefined, "prediction", undefined, "prefetch");
    const demand = scheduler.fetch("rotated-url", range, undefined, "prediction");
    expect(scheduler.statistics()).toMatchObject({
      activeNetwork: 0, activePersistent: 1, queuedPersistent: 2,
      queuedCritical: 1, queuedPrefetch: 1, priorityPromotions: 1,
    });
    gate.resolve(new ArrayBuffer(4));
    const results = await Promise.all([active, unrelated, prediction, demand]);
    expect(results[2]).toBe(results[3]);
    expect(persistent.read.mock.calls.map(([key]) => key.split("\0")[0])).toEqual([
      "immutable:active", "immutable:prediction", "immutable:unrelated",
    ]);
    expect(network).not.toHaveBeenCalled();
    expect(scheduler.statistics()).toMatchObject({ active: 0, queued: 0, prefetchUsefulRequests: 1 });
  });

  it("releases a disk miss before it waits for the occupied network slot", async () => {
    const gate = deferred<Response>();
    const network = vi.fn(async () => (await gate.promise).clone());
    vi.stubGlobal("fetch", network);
    const persistent = cacheWithRead(async (key) => key.includes("miss") ? null : new ArrayBuffer(4));
    const scheduler = new GsTileRangeScheduler(1, 16, 0, persistent, 1);
    const active = scheduler.fetch("network", range);
    const miss = scheduler.fetch("miss", range, undefined, "miss");
    await vi.waitFor(() => expect(scheduler.statistics().queuedNetwork).toBe(1));
    const hit = await scheduler.fetch("hit", range, undefined, "hit");
    expect(hit.byteLength).toBe(4);
    expect(scheduler.statistics()).toMatchObject({ activePersistent: 0, activeNetwork: 1, persistentCacheHits: 1 });
    gate.resolve(response());
    await Promise.all([active, miss]);
    expect(network).toHaveBeenCalledTimes(2);
    expect(scheduler.statistics()).toMatchObject({ active: 0, queued: 0 });
  });

  it("cancels queued disk work without reading it or leaking a slot", async () => {
    vi.useFakeTimers();
    const gate = deferred<ArrayBuffer | null>();
    const persistent = cacheWithRead(() => gate.promise);
    const network = vi.fn(async () => response());
    vi.stubGlobal("fetch", network);
    const scheduler = new GsTileRangeScheduler(1, 16, 0, persistent, 1);
    const active = scheduler.fetch("active", range, undefined, "active");
    const controller = new AbortController();
    const cancelled = scheduler.fetch("cancelled", range, controller.signal, "cancelled");
    controller.abort(new DOMException("superseded", "AbortError"));
    await expect(cancelled).rejects.toMatchObject({ name: "AbortError" });
    await vi.advanceTimersByTimeAsync(0);
    expect(scheduler.statistics()).toMatchObject({ activePersistent: 1, queued: 0 });
    gate.resolve(new ArrayBuffer(4));
    await active;
    expect(persistent.read).toHaveBeenCalledOnce();
    expect(network).not.toHaveBeenCalled();
    expect(scheduler.statistics()).toMatchObject({ active: 0, queued: 0 });
  });

  it("does not fetch after an active disk read is cancelled, even if the adapter returns a miss", async () => {
    vi.useFakeTimers();
    const gate = deferred<ArrayBuffer | null>();
    const persistent = cacheWithRead(() => gate.promise);
    const network = vi.fn(async () => response());
    vi.stubGlobal("fetch", network);
    const scheduler = new GsTileRangeScheduler(1, 16, 0, persistent, 1);
    const controller = new AbortController();
    const cancelled = scheduler.fetch("cancelled", range, controller.signal, "cancelled");
    await vi.advanceTimersByTimeAsync(0);
    controller.abort(new DOMException("superseded", "AbortError"));
    await expect(cancelled).rejects.toMatchObject({ name: "AbortError" });
    await vi.advanceTimersByTimeAsync(0);
    gate.resolve(null);
    await vi.advanceTimersByTimeAsync(0);
    expect(network).not.toHaveBeenCalled();
    expect(scheduler.statistics()).toMatchObject({ active: 0, queued: 0, persistentErrors: 0, cacheBytes: 0 });
  });

  it("falls back from storage failure without consuming the disk pool during network retry", async () => {
    vi.useFakeTimers();
    const persistent = cacheWithRead(async () => { throw new Error("storage unavailable"); });
    let calls = 0;
    vi.stubGlobal("fetch", vi.fn(async () => {
      calls += 1;
      return calls === 1 ? new Response(null, { status: 503 }) : response();
    }));
    const scheduler = new GsTileRangeScheduler(1, 16, 0, persistent, 1);
    const pending = scheduler.fetch("tile", range, undefined, "tile");
    await vi.advanceTimersByTimeAsync(1);
    expect(scheduler.statistics()).toMatchObject({ active: 0, persistentErrors: 1, networkRetries: 1 });
    await vi.advanceTimersByTimeAsync(1000);
    expect((await pending).byteLength).toBe(4);
    expect(persistent.read).toHaveBeenCalledOnce();
    expect(scheduler.statistics()).toMatchObject({ active: 0, queued: 0 });
  });
});
