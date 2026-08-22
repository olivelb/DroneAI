import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchGsTileRange } from "./range-source";

describe("GSTile range source", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("requires a matching partial response", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
      new Response(new Uint8Array([1, 2, 3, 4]), {
        status: 206,
        headers: { "Content-Range": "bytes 32-35/128" },
      }),
    );
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
});
