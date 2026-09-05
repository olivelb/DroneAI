import type {
  MissionCatalogItem,
  MissionDetail,
  MissionSummary,
} from "./types";

const timestamp = (value?: string | null) => {
  const parsed = value ? Date.parse(value) : Number.NaN;
  return Number.isFinite(parsed) ? parsed / 1000 : 0;
};

export const autoSelectMission = (
  map: Record<string, MissionSummary>,
  preferred?: string | null,
): string | null => {
  if (preferred && map[preferred]) return preferred;
  const sorted = Object.values(map).sort(
    (left, right) => right.updated_at - left.updated_at,
  );
  const running = sorted.find(
    (mission) => mission.overall_status === "processing",
  );
  return running?.vol_id ?? sorted[0]?.vol_id ?? null;
};

export const missionSummaryFromCatalog = (
  mission: MissionCatalogItem,
): MissionSummary => ({
  vol_id: mission.vol_id,
  services: {},
  logs: [],
  updated_at: timestamp(mission.updated_at),
  overall_status: mission.overall_status,
  status: mission.status,
  current_step: mission.current_step,
  progress: mission.progress,
  quality_profile: mission.quality_profile,
  is_stale: mission.is_stale,
  last_event_age_seconds: mission.last_event_age_seconds,
});

export const missionSummaryFromDetail = (
  mission: MissionDetail,
): MissionSummary => ({
  ...missionSummaryFromCatalog(mission),
  services: mission.phases ?? {},
  logs: mission.logs.map((entry) => ({
    message:
      entry.message ??
      [entry.service, entry.step, entry.status].filter(Boolean).join(" · "),
    service: entry.service ?? undefined,
    step: entry.step ?? undefined,
    status: entry.status ?? undefined,
    ts: timestamp(entry.created_at),
  })),
  stage_runs: mission.stage_runs ?? [],
  parameters: mission.parameters,
  products: mission.products,
});

export const mergeMissionSnapshots = (
  previous: Record<string, MissionSummary>,
  incoming: Record<string, MissionSummary>,
): Record<string, MissionSummary> =>
  Object.fromEntries(
    Object.entries(incoming).map(([volId, mission]) => [
      volId,
      (previous[volId]?.updated_at ?? 0) > mission.updated_at
        ? previous[volId]
        : mission,
    ]),
  );

export const summaryLogMessages = (mission: MissionSummary): string[] =>
  mission.logs.map((entry) => entry.message).slice(-100);


export const catalogueWithSelectedDetail = (
  items: MissionCatalogItem[], previous: Record<string, MissionSummary>, selected: string | null,
): Record<string, MissionSummary> => {
  const map = Object.fromEntries(items.map((item) => {
    const summary = missionSummaryFromCatalog(item);
    const detail = item.vol_id === selected ? previous[item.vol_id] : undefined;
    return [item.vol_id, detail?.stage_runs ? {
      ...detail, ...summary, services: detail.services, logs: detail.logs,
      stage_runs: detail.stage_runs, parameters: detail.parameters, products: detail.products,
    } : summary];
  }));
  if (selected && !map[selected] && previous[selected]) map[selected] = previous[selected];
  return map;
};
