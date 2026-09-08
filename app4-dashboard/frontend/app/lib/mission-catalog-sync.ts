import { fetchMissionCatalogIndex, fetchMissionCatalogItems } from "./api";
import type { MissionCatalogItem } from "./types";

/** One instance per authenticated runtime; never persists across identities. */
export class MissionCatalogSynchronizer {
  private items = new Map<string, MissionCatalogItem>();
  private versions: Record<string, string> = {};
  private pending: Promise<MissionCatalogItem[]> | null = null;

  constructor(
    private readonly readIndex = fetchMissionCatalogIndex,
    private readonly readItems = fetchMissionCatalogItems,
  ) {}

  refresh(signal?: AbortSignal): Promise<MissionCatalogItem[]> {
    if (this.pending) return this.pending;
    this.pending = this.synchronize(signal).finally(() => { this.pending = null; });
    return this.pending;
  }

  private async synchronize(signal?: AbortSignal): Promise<MissionCatalogItem[]> {
    const index = await this.readIndex(signal);
    const ids = Object.keys(index.versions);
    const changed = ids.filter((id) =>
      this.versions[id] !== index.versions[id] || !this.items.has(id));
    const next = new Map(this.items);
    for (let offset = 0; offset < changed.length; offset += 100) {
      const batch = changed.slice(offset, offset + 100);
      const returned = await this.readItems(batch, signal);
      for (const id of batch) next.delete(id);
      const requested = new Set(batch);
      for (const item of returned) if (requested.has(item.vol_id)) next.set(item.vol_id, item);
    }
    signal?.throwIfAborted();
    const visible = new Set(ids);
    for (const id of next.keys()) if (!visible.has(id)) next.delete(id);
    // Freshness changes with time even if no database revision changes.
    for (const [id, item] of next) {
      const timestamp = item.updated_at ? Date.parse(item.updated_at) / 1000 : NaN;
      const age = Number.isFinite(timestamp) ? Math.max(0, index.observed_at - timestamp) : null;
      next.set(id, { ...item, last_event_age_seconds: age,
        is_stale: item.overall_status === "processing" && age !== null && age > index.stale_after_seconds });
    }
    // Commit only after every request succeeds; missing items are retried next poll.
    this.items = next;
    this.versions = index.versions;
    return [...next.values()];
  }
}
