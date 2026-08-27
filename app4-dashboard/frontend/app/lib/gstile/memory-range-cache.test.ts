import { describe, expect, it, vi } from "vitest";
import { GsTileMemoryRangeCache } from "./memory-range-cache";

describe("GSTile byte-bounded segmented cache", () => {
  it.each([-1, 0.5, NaN, Infinity])("rejects an invalid cap: %s", (cap) => {
    expect(() => new GsTileMemoryRangeCache(cap, () => undefined)).toThrow();
  });

  it("does not retain data when disabled or too large", () => {
    const disabled = new GsTileMemoryRangeCache(0, () => undefined);
    expect(disabled.put("a", new ArrayBuffer(1), "critical")).toBe(false);
    const cache = new GsTileMemoryRangeCache(8, () => undefined);
    expect(cache.put("a", new ArrayBuffer(9), "critical")).toBe(false);
    expect(cache.bytes).toBe(0);
  });

  it("protects a prediction only once it is demanded", () => {
    const evict = vi.fn();
    const cache = new GsTileMemoryRangeCache(16, evict);
    const predicted = new ArrayBuffer(4);
    cache.put("predicted", predicted, "prefetch");
    expect(cache.protectedBytes).toBe(0);
    expect(cache.get("predicted", "critical")).toBe(predicted);
    expect(cache.protectedBytes).toBe(4);
    for (let index = 0; index < 8; index += 1) {
      cache.put(`scan-${index}`, new ArrayBuffer(4), "prefetch");
    }
    expect(cache.get("predicted", "critical")).toBe(predicted);
    expect(evict).not.toHaveBeenCalledWith("predicted");
    expect(cache.size).toBe(4);
  });

  it("does not make protected entries more recent on speculative hits", () => {
    const cache = new GsTileMemoryRangeCache(16, () => undefined);
    for (const key of ["a", "b", "c"]) cache.put(key, new ArrayBuffer(4), "critical");
    cache.get("a", "prefetch");
    cache.put("d", new ArrayBuffer(4), "critical");
    cache.put("e", new ArrayBuffer(4), "prefetch");
    expect(cache.has("a")).toBe(false);
    expect(cache.has("b")).toBe(true);
  });

  it("refreshes demanded entries and demotes the least recent within the byte cap", () => {
    const cache = new GsTileMemoryRangeCache(16, () => undefined);
    for (const key of ["a", "b", "c"]) cache.put(key, new ArrayBuffer(4), "critical");
    cache.get("a", "critical");
    cache.put("d", new ArrayBuffer(4), "critical");
    cache.put("e", new ArrayBuffer(4), "prefetch");
    expect(cache.has("a")).toBe(true);
    expect(cache.has("b")).toBe(false);
    expect(cache.protectedBytes).toBe(12);
  });

  it("rejects an oversized prediction without partially evicting other data", () => {
    const evict = vi.fn();
    const cache = new GsTileMemoryRangeCache(16, evict);
    cache.put("demand", new ArrayBuffer(12), "critical");
    cache.put("small", new ArrayBuffer(4), "prefetch");
    expect(cache.put("large", new ArrayBuffer(5), "prefetch")).toBe(false);
    expect(cache.has("small")).toBe(true);
    expect(cache.bytes).toBe(16);
    expect(evict).not.toHaveBeenCalled();
  });

  it("admits large demanded entries and accounts replacements exactly", () => {
    const cache = new GsTileMemoryRangeCache(16, () => undefined);
    cache.put("old", new ArrayBuffer(12), "critical");
    const large = new ArrayBuffer(16);
    expect(cache.put("large", large, "critical")).toBe(true);
    expect(cache.get("large", "critical")).toBe(large);
    expect(cache.has("old")).toBe(false);
    expect(cache.bytes).toBe(16);
    cache.put("large", new ArrayBuffer(3), "critical");
    expect(cache.bytes).toBe(3);
    expect(cache.protectedBytes).toBe(3);
    expect(cache.size).toBe(1);
  });

  it("preserves byte accounting and buffer identity over a deterministic mixed trace", () => {
    const evicted: string[] = [];
    const cache = new GsTileMemoryRangeCache(97, (key) => evicted.push(key));
    const retained = new Map<string, ArrayBuffer>();
    let state = 42;
    const next = () => { state = (Math.imul(state, 1664525) + 1013904223) >>> 0; return state; };
    for (let step = 0; step < 1000; step += 1) {
      const key = String(next() % 31);
      const priority = next() % 3 === 0 ? "prefetch" : "critical";
      if (step % 4 === 0) {
        expect(cache.get(key, priority)).toBe(retained.get(key));
      } else {
        const buffer = new Uint8Array((next() % 110) + 1).fill(step % 256).buffer;
        const admitted = cache.put(key, buffer, priority);
        for (const removed of evicted.splice(0)) retained.delete(removed);
        if (admitted) retained.set(key, buffer);
      }
      expect(cache.bytes).toBe([...retained.values()].reduce((sum, value) => sum + value.byteLength, 0));
      expect(cache.bytes).toBeLessThanOrEqual(97);
      expect(cache.protectedBytes).toBeLessThanOrEqual(Math.floor(97 * 0.75));
      expect(cache.size).toBe(retained.size);
    }
  });
});
