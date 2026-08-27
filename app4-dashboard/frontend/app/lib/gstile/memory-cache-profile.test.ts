import { describe, expect, it } from "vitest";
import {
  DEFAULT_GSTILE_MEMORY_CACHE_BYTES,
  gstileMemoryCacheBytes,
} from "./range-source";
import { GsTileMemoryRangeCache } from "./memory-range-cache";

describe("GSTile explicit memory cache profile", () => {
  it.each([null, undefined, "standard", "", "Desktop", "1536", "Infinity"])(
    "keeps the conservative default for %s", (value) => {
      expect(gstileMemoryCacheBytes(value)).toBe(768 * 1024 * 1024);
      expect(gstileMemoryCacheBytes(value)).toBe(DEFAULT_GSTILE_MEMORY_CACHE_BYTES);
    },
  );

  it("opts into a fixed 1.5 GiB desktop cap without eager allocation", () => {
    const bytes = gstileMemoryCacheBytes("desktop");
    expect(bytes).toBe(1536 * 1024 * 1024);
    const cache = new GsTileMemoryRangeCache(bytes, () => undefined);
    expect(cache.bytes).toBe(0);
    expect(cache.size).toBe(0);
  });

  it.each(["standard", "desktop"])("bounds %s under a scaled revisit and speculative scan", (profile) => {
    // Scale bytes to avoid GiB allocations in unit tests; retain the real SLRU.
    const cap = gstileMemoryCacheBytes(profile) / (1024 * 1024);
    const cache = new GsTileMemoryRangeCache(cap, () => undefined);
    const buffer = new ArrayBuffer(100);
    for (let index = 0; index < 13; index += 1) cache.put(`view-${index}`, buffer, "critical");
    if (profile === "desktop") {
      for (let index = 0; index < 13; index += 1) expect(cache.has(`view-${index}`)).toBe(true);
    } else expect(cache.has("view-0")).toBe(false);
    cache.get("view-12", "critical");
    for (let index = 0; index < 30; index += 1) {
      cache.put(`prediction-${index}`, buffer, "prefetch");
      expect(cache.bytes).toBeLessThanOrEqual(cap);
      expect(cache.protectedBytes).toBeLessThanOrEqual(Math.floor(cap * 0.75));
      expect(cache.get("view-12", "critical")).toBe(buffer);
    }
  });
});
