import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchGsTileRange, GsTileRangeScheduler } from "./range-source";

describe("GSTile range source", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("requires a matching partial response", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => {
      void _input;
      void _init;
      return new Response(new Uint8Array([1, 2, 3, 4]), {
        status: 206,
        headers: { "Content-Range": "bytes 32-35/128" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    const result = await fetchGsTileRange("https://example.test/r.gst", {
      start: 32,
      length: 4,
    });
    expect(Array.from(new Uint8Array(result))).toEqual([1, 2, 3, 4]);
    expect(fetchMock.mock.calls[0]?.[1]?.headers).toEqual({ Range: "bytes=32-35" });
  });

  it("rejects a server that ignores Range", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(new Uint8Array(4), { status: 200 })));
    await expect(
      fetchGsTileRange("https://example.test/r.gst", { start: 32, length: 4 }),
    ).rejects.toThrow(/HTTP 200/);
  });

  it("deduplicates and caches identical tile ranges", async () => {
    const fetchMock = vi.fn(async () =>
      new Response(new Uint8Array([1, 2, 3, 4]), {
        status: 206,
        headers: { "Content-Range": "bytes 32-35/128" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const scheduler = new GsTileRangeScheduler(2, 1024);
    const range = { start: 32, length: 4 };

    const [first, second] = await Promise.all([
      scheduler.fetch("https://example.test/r.gst", range),
      scheduler.fetch("https://example.test/r.gst", range),
    ]);
    const third = await scheduler.fetch("https://example.test/r.gst", range);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(Array.from(new Uint8Array(first))).toEqual([1, 2, 3, 4]);
    expect(second).toBe(first);
    expect(third).toBe(first);
    expect(scheduler.statistics()).toMatchObject({
      cacheHits: 1,
      cacheMisses: 1,
      inFlightHits: 1,
      cacheEntries: 1,
      cacheBytes: 4,
    });
  });

  it("keeps a shared transfer alive when a superseded selection aborts", async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const fetchMock = vi.fn(async () => {
      await gate;
      return new Response(new Uint8Array([1, 2, 3, 4]), {
        status: 206,
        headers: { "Content-Range": "bytes 32-35/128" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    const scheduler = new GsTileRangeScheduler(1, 1024);
    const firstController = new AbortController();
    const first = scheduler.fetch("https://example.test/r.gst", { start: 32, length: 4 }, firstController.signal);
    const second = scheduler.fetch("https://example.test/r.gst", { start: 32, length: 4 });
    firstController.abort(new DOMException("superseded", "AbortError"));
    release();

    await expect(first).rejects.toMatchObject({ name: "AbortError" });
    await expect(second).resolves.toHaveProperty("byteLength", 4);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("reuses an orphaned transfer when the next camera selection needs it", async () => {
    vi.useFakeTimers();
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const fetchMock = vi.fn(async () => {
      await gate;
      return new Response(new Uint8Array([1, 2, 3, 4]), {
        status: 206,
        headers: { "Content-Range": "bytes 32-35/128" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    const scheduler = new GsTileRangeScheduler(1, 1024, 300);
    const firstController = new AbortController();
    const first = scheduler.fetch(
      "https://example.test/r.gst",
      { start: 32, length: 4 },
      firstController.signal,
    );

    firstController.abort(new DOMException("superseded", "AbortError"));
    await expect(first).rejects.toMatchObject({ name: "AbortError" });
    await vi.advanceTimersByTimeAsync(200);
    const replacement = scheduler.fetch("https://example.test/r.gst", {
      start: 32,
      length: 4,
    });
    await vi.advanceTimersByTimeAsync(200);
    release();

    await expect(replacement).resolves.toHaveProperty("byteLength", 4);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("cancels an obsolete transfer and releases its concurrency slot", async () => {
    vi.useFakeTimers();
    const calls: string[] = [];
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) =>
        new Promise<Response>((resolve, reject) => {
          const url = String(input);
          calls.push(url);
          const abort = () => reject(init?.signal?.reason);
          if (init?.signal?.aborted) {
            abort();
            return;
          }
          init?.signal?.addEventListener("abort", abort, { once: true });
          if (url.endsWith("second.gst")) {
            init?.signal?.removeEventListener("abort", abort);
            resolve(
              new Response(new Uint8Array([1, 2, 3, 4]), {
                status: 206,
                headers: { "Content-Range": "bytes 32-35/128" },
              }),
            );
          }
        }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const scheduler = new GsTileRangeScheduler(1, 1024, 300);
    const controller = new AbortController();
    const obsolete = scheduler.fetch(
      "https://example.test/obsolete.gst",
      { start: 32, length: 4 },
      controller.signal,
    );
    const current = scheduler.fetch("https://example.test/second.gst", {
      start: 32,
      length: 4,
    });

    controller.abort(new DOMException("superseded", "AbortError"));

    await expect(obsolete).rejects.toMatchObject({ name: "AbortError" });
    await vi.advanceTimersByTimeAsync(300);
    await expect(current).resolves.toHaveProperty("byteLength", 4);
    expect(calls).toEqual([
      "https://example.test/obsolete.gst",
      "https://example.test/second.gst",
    ]);
    expect(scheduler.statistics()).toMatchObject({ active: 0, queued: 0 });
  });
});
