import { afterEach, describe, expect, it, vi } from "vitest";
import { createServer } from "node:http";
import { fetchGsTileRange, GsTileRangeScheduler } from "./range-source";

const range = { start: 0, length: 4 };
const goodResponse = () => new Response(new Uint8Array([1, 2, 3, 4]), {
  status: 206, headers: { "Content-Range": "bytes 0-3/4" },
});

describe("GSTile bounded network recovery", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it.each([408, 429, 500, 502, 503, 504, "network"] as const)(
    "recovers from %s without caching the failed attempt", async (failure) => {
      vi.useFakeTimers();
      const fetchMock = vi.fn()
        .mockImplementationOnce(async () => {
          if (failure === "network") throw new TypeError("Failed to fetch");
          return new Response(null, { status: failure });
        })
        .mockImplementation(goodResponse);
      vi.stubGlobal("fetch", fetchMock);
      const scheduler = new GsTileRangeScheduler(1, 1024);
      const pending = scheduler.fetch("https://example.test/r.gst", range);
      await vi.runAllTimersAsync();
      expect(new Uint8Array(await pending)).toEqual(new Uint8Array([1, 2, 3, 4]));
      expect(fetchMock).toHaveBeenCalledTimes(2);
      expect(scheduler.statistics()).toMatchObject({
        active: 0, queued: 0, networkRetries: 1, networkBytes: 4, cacheBytes: 4,
      });
    },
  );

  it("bounds retries and does not count a failed promoted prefetch as useful", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn(async () => new Response(null, { status: 503 }));
    vi.stubGlobal("fetch", fetchMock);
    const scheduler = new GsTileRangeScheduler(1, 1024);
    const prefetch = scheduler.fetch("https://example.test/r.gst", range,
      undefined, undefined, undefined, "prefetch");
    const visible = scheduler.fetch("https://example.test/r.gst", range);
    const results = Promise.allSettled([prefetch, visible]);
    await vi.runAllTimersAsync();
    expect((await results).map((result) => result.status)).toEqual(["rejected", "rejected"]);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(scheduler.statistics()).toMatchObject({
      active: 0, queued: 0, priorityPromotions: 1, networkRetries: 2,
      cacheBytes: 0, prefetchCompletedBytes: 0, prefetchUsefulBytes: 0,
    });
  });

  it("recovers a real HTTP range transfer after a transient server response", async () => {
    const ranges: (string | undefined)[] = [];
    const server = createServer((request, response) => {
      ranges.push(request.headers.range);
      if (ranges.length === 1) {
        response.writeHead(503, { "Retry-After": "0" });
        response.end();
      } else {
        response.writeHead(206, { "Content-Range": "bytes 0-3/4" });
        response.end(new Uint8Array([1, 2, 3, 4]));
      }
    });
    await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
    try {
      const address = server.address();
      if (!address || typeof address === "string") throw new Error("Missing HTTP port");
      const scheduler = new GsTileRangeScheduler(1, 1024);
      const content = await scheduler.fetch(`http://127.0.0.1:${address.port}/r.gst`, range);
      expect(new Uint8Array(content)).toEqual(new Uint8Array([1, 2, 3, 4]));
      expect(ranges).toEqual(["bytes=0-3", "bytes=0-3"]);
      expect(scheduler.statistics()).toMatchObject({ networkRetries: 1, active: 0 });
    } finally {
      server.closeAllConnections();
      await new Promise<void>((resolve) => server.close(() => resolve()));
    }
  });

  it.each([200, 401, 403, 404, 416, 501])("does not retry HTTP %s", async (status) => {
    const fetchMock = vi.fn(async () => new Response(null, { status }));
    vi.stubGlobal("fetch", fetchMock);
    const scheduler = new GsTileRangeScheduler(1);
    await expect(scheduler.fetch("https://example.test/r.gst", range)).rejects.toThrow(`HTTP ${status}`);
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it.each(["bytes 0-3/3", "bytes 1-4/8", "bytes 0-3/9007199254740992"])(
    "rejects malformed range %s without retrying", async (contentRange) => {
      const fetchMock = vi.fn(async () => new Response(new Uint8Array(4), {
        status: 206, headers: { "Content-Range": contentRange },
      }));
      vi.stubGlobal("fetch", fetchMock);
      await expect(new GsTileRangeScheduler(1).fetch("https://example.test/r.gst", range))
        .rejects.toThrow(/Content-Range/);
      expect(fetchMock).toHaveBeenCalledOnce();
    },
  );

  it("rejects unsafe endpoints before fetching", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    await expect(fetchGsTileRange("https://example.test/r.gst", {
      start: Number.MAX_SAFE_INTEGER, length: 2,
    })).rejects.toThrow(/Invalid GSTile byte range/);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("honors a short Retry-After while freeing the slot for visible tiles", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(null, { status: 503, headers: { "Retry-After": "1" } }))
      .mockImplementation(goodResponse);
    vi.stubGlobal("fetch", fetchMock);
    const scheduler = new GsTileRangeScheduler(1, 1024);
    const prefetch = scheduler.fetch("https://example.test/p.gst", range,
      undefined, undefined, undefined, "prefetch");
    await vi.advanceTimersByTimeAsync(0);
    expect(scheduler.statistics()).toMatchObject({ active: 0, networkRetries: 1 });
    await scheduler.fetch("https://example.test/visible.gst", range);
    await vi.advanceTimersByTimeAsync(999);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(1);
    await prefetch;
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(scheduler.statistics().active).toBe(0);
  });

  it("does not retry before a Retry-After exceeding the local wait budget", async () => {
    const fetchMock = vi.fn(async () => new Response(null, {
      status: 429, headers: { "Retry-After": "60" },
    }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(new GsTileRangeScheduler(1).fetch("https://example.test/r.gst", range))
      .rejects.toThrow(/HTTP 429/);
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("cancels a sleeping retry without another fetch or a leaked slot", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn(async () => new Response(null, { status: 503 }));
    vi.stubGlobal("fetch", fetchMock);
    const scheduler = new GsTileRangeScheduler(1, 1024, 0);
    const controller = new AbortController();
    const pending = scheduler.fetch("https://example.test/r.gst", range, controller.signal);
    const settled = Promise.allSettled([pending]);
    await vi.advanceTimersByTimeAsync(0);
    controller.abort();
    await vi.runAllTimersAsync();
    expect((await settled)[0].status).toBe("rejected");
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(scheduler.statistics()).toMatchObject({ active: 0, queued: 0, cacheBytes: 0 });
  });
});
