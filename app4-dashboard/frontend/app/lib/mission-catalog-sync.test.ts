import { describe, expect, it, vi } from "vitest";
import { MissionCatalogSynchronizer } from "./mission-catalog-sync";
import { catalogueWithSelectedDetail } from "./mission-runtime-state";
import type { MissionCatalogItem } from "./types";

const item = (vol_id: string): MissionCatalogItem => ({ vol_id, owner_subject: "alice",
  status: "processing", progress: 0, pipeline: "modern", attempt_count: 1,
  overall_status: "processing", is_stale: false, updated_at: "2026-01-01T00:00:00Z" });
const index = (versions: Record<string, string>, age = 0) => ({ versions,
  observed_at: Date.parse("2026-01-01T00:00:00Z") / 1000 + age, stale_after_seconds: 120 });

describe("mission catalogue synchronization", () => {
  it("loads 20k once, fetches no unchanged items, then fetches only changed rows", async () => {
    const versions = Object.fromEntries(Array.from({ length: 20_000 }, (_, i) => [`m-${i}`, "v1"]));
    const readIndex = vi.fn().mockResolvedValue(index(versions));
    const readItems = vi.fn(async (ids: string[]) => ids.map(item));
    const sync = new MissionCatalogSynchronizer(readIndex, readItems);
    expect(await sync.refresh()).toHaveLength(20_000);
    expect(readItems).toHaveBeenCalledTimes(200);
    readItems.mockClear();
    expect(await sync.refresh()).toHaveLength(20_000);
    expect(readItems).not.toHaveBeenCalled();
    readIndex.mockResolvedValue(index({ ...versions, "m-123": "v2" }));
    await sync.refresh();
    expect(readItems).toHaveBeenCalledExactlyOnceWith(["m-123"], undefined);
  });

  it("removes deleted rows and recomputes staleness without downloading unchanged items", async () => {
    const readIndex = vi.fn().mockResolvedValue(index({ a: "v1", b: "v1" }));
    const readItems = vi.fn(async (ids: string[]) => ids.map(item));
    const sync = new MissionCatalogSynchronizer(readIndex, readItems);
    await sync.refresh();
    readItems.mockClear();
    readIndex.mockResolvedValue(index({ a: "v1" }, 121));
    const result = await sync.refresh();
    expect(result).toHaveLength(1);
    expect(result[0]).toMatchObject({ vol_id: "a", is_stale: true, last_event_age_seconds: 121 });
    expect(readItems).not.toHaveBeenCalled();
    expect(catalogueWithSelectedDetail([], { b: { vol_id: "b", services: {}, logs: [],
      updated_at: 0, overall_status: "processing" } }, "b", false)).toEqual({});
  });

  it("retries rows temporarily absent from the item response", async () => {
    const readIndex = vi.fn().mockResolvedValue(index({ a: "v1" }));
    const readItems = vi.fn().mockResolvedValueOnce([]).mockResolvedValue([item("a")]);
    const sync = new MissionCatalogSynchronizer(readIndex, readItems);
    expect(await sync.refresh()).toEqual([]);
    expect(await sync.refresh()).toEqual([expect.objectContaining({ vol_id: "a" })]);
    expect(readItems).toHaveBeenCalledTimes(2);
  });

  it("does not commit partial pages or failed revisions", async () => {
    const versions = Object.fromEntries(Array.from({ length: 101 }, (_, i) => [`m-${i}`, "v1"]));
    const readIndex = vi.fn().mockResolvedValue(index(versions));
    const readItems = vi.fn(async (ids: string[]) => ids.map(item));
    readItems.mockImplementationOnce(async (ids) => ids.map(item)).mockRejectedValueOnce(new Error("offline"));
    const sync = new MissionCatalogSynchronizer(readIndex, readItems);
    await expect(sync.refresh()).rejects.toThrow("offline");
    readItems.mockClear();
    expect(await sync.refresh()).toHaveLength(101);
    expect(readItems).toHaveBeenCalledTimes(2);
  });

  it("coalesces concurrent refresh and isolates a new authenticated runtime", async () => {
    const readIndex = vi.fn().mockResolvedValue(index({ a: "v1" }));
    const readItems = vi.fn(async (ids: string[]) => ids.map(item));
    const sync = new MissionCatalogSynchronizer(readIndex, readItems);
    const first = sync.refresh();
    expect(sync.refresh()).toBe(first);
    await first;
    expect(readIndex).toHaveBeenCalledTimes(1);
    await new MissionCatalogSynchronizer(readIndex, readItems).refresh();
    expect(readItems).toHaveBeenCalledTimes(2);
  });

  it("aborted refresh cannot commit cached data", async () => {
    const readIndex = vi.fn().mockResolvedValue(index({ a: "v1" }));
    const readItems = vi.fn(async (ids: string[]) => ids.map(item));
    const sync = new MissionCatalogSynchronizer(readIndex, readItems);
    const abort = new AbortController(); abort.abort();
    await expect(sync.refresh(abort.signal)).rejects.toThrow();
    await sync.refresh();
    expect(readItems).toHaveBeenCalledTimes(2);
  });
});
