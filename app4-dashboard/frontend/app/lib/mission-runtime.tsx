"use client";

import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import { fetchMissionCatalog, fetchMissionDetail, getWsBaseUrl } from "./api";
import { useAuth } from "./auth";
import type {
  MissionCatalogItem,
  MissionDetail,
  MissionLog,
  MissionSummary,
  StatusPayload,
} from "./types";
import { overallStatusFor } from "./types";

type MissionRuntimeState = {
  missions: Record<string, MissionSummary>;
  activeMissionId: string | null;
  setActiveMissionId: (id: string | null) => void;
  activeMission: MissionSummary | null;
  logs: string[];
  setLogs: React.Dispatch<React.SetStateAction<string[]>>;
  wsConnected: boolean;
  refreshSummary: () => void;
};

const MissionRuntimeContext = createContext<MissionRuntimeState | null>(null);

export function useMissionRuntime() {
  const context = useContext(MissionRuntimeContext);
  if (!context) {
    throw new Error(
      "useMissionRuntime must be used within MissionRuntimeProvider",
    );
  }
  return context;
}

const timestamp = (value?: string | null) => {
  const parsed = value ? Date.parse(value) : Number.NaN;
  return Number.isFinite(parsed) ? parsed / 1000 : 0;
};

const autoSelectMission = (
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

export function MissionRuntimeProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const { authStatus } = useAuth();
  const [missions, setMissions] = useState<Record<string, MissionSummary>>({});
  const [activeMissionId, setActiveMissionId] = useState<string | null>(null);
  const [logs, setLogs] = useState<string[]>([]);
  const [wsConnected, setWsConnected] = useState(false);
  const missionsRef = useRef<Record<string, MissionSummary>>({});
  const activeVolIdRef = useRef<string | null>(null);
  const detailRequestRef = useRef(0);

  const refreshSelectedMission = useCallback(async (volId: string) => {
    const request = ++detailRequestRef.current;
    try {
      const detail = await fetchMissionDetail(volId);
      if (
        request !== detailRequestRef.current ||
        activeVolIdRef.current !== volId
      ) {
        return;
      }
      const selected = missionSummaryFromDetail(detail);
      const next = { ...missionsRef.current, [volId]: selected };
      missionsRef.current = next;
      setMissions(next);
      setLogs(summaryLogMessages(selected));
    } catch (error) {
      console.error(`Mission detail error for ${volId}:`, error);
    }
  }, []);

  const selectMission = useCallback(
    (id: string | null) => {
      activeVolIdRef.current = id;
      setActiveMissionId(id);
      setLogs([]);
      if (id) void refreshSelectedMission(id);
    },
    [refreshSelectedMission],
  );

  const refreshSummary = useCallback(async () => {
    try {
      const catalog = await fetchMissionCatalog(100, 0);
      const current = activeVolIdRef.current;
      const map = Object.fromEntries(
        catalog.items.map((mission) => {
          const summary = missionSummaryFromCatalog(mission);
          const selectedDetail =
            mission.vol_id === current
              ? missionsRef.current[mission.vol_id]
              : undefined;
          return [
            mission.vol_id,
            selectedDetail?.stage_runs
              ? {
                  ...selectedDetail,
                  ...summary,
                  services: selectedDetail.services,
                  logs: selectedDetail.logs,
                  stage_runs: selectedDetail.stage_runs,
                  parameters: selectedDetail.parameters,
                  products: selectedDetail.products,
                }
              : summary,
          ];
        }),
      );
      const selected = autoSelectMission(map, current);
      missionsRef.current = map;
      setMissions(map);
      if (selected !== current) {
        activeVolIdRef.current = selected;
        setActiveMissionId(selected);
        setLogs([]);
      }
      if (selected) await refreshSelectedMission(selected);
    } catch (error) {
      console.error("Mission catalog error:", error);
    }
  }, [refreshSelectedMission]);

  useEffect(() => {
    if (authStatus !== "authenticated") return;
    const initialLoad = window.setTimeout(() => void refreshSummary(), 0);
    const interval = window.setInterval(() => void refreshSummary(), 3_000);
    return () => {
      window.clearTimeout(initialLoad);
      window.clearInterval(interval);
    };
  }, [authStatus, refreshSummary]);

  useEffect(() => {
    activeVolIdRef.current = activeMissionId;
  }, [activeMissionId]);

  useEffect(() => {
    if (authStatus !== "authenticated") return;
    let ws: WebSocket | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let closed = false;

    const connect = () => {
      ws = new WebSocket(`${getWsBaseUrl()}/ws/status`);
      ws.onopen = () => setWsConnected(true);
      ws.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data) as StatusPayload;
          if (!payload.vol_id) return;
          const existing = missionsRef.current[payload.vol_id];
          if (!existing || existing.stage_runs?.length) return;
          const services = {
            ...existing.services,
            ...(payload.service ? { [payload.service]: payload } : {}),
          };
          const missionLogs = payload.log
            ? [
                ...existing.logs.slice(-199),
                {
                  message: payload.log,
                  service: payload.service,
                  step: payload.step,
                  status: payload.status,
                  ts: Date.now() / 1000,
                } as MissionLog,
              ]
            : existing.logs;
          const mission: MissionSummary = {
            ...existing,
            services,
            logs: missionLogs,
            updated_at: Date.now() / 1000,
            overall_status: overallStatusFor(services),
          };
          const next = {
            ...missionsRef.current,
            [payload.vol_id]: mission,
          };
          missionsRef.current = next;
          setMissions(next);
          if (activeVolIdRef.current === payload.vol_id) {
            setLogs(summaryLogMessages(mission));
          }
        } catch (error) {
          console.error("WS parse error:", error);
        }
      };
      ws.onclose = () => {
        setWsConnected(false);
        if (!closed) timer = setTimeout(connect, 1000);
      };
      ws.onerror = (error) => console.error("WS error:", error);
    };

    connect();
    return () => {
      closed = true;
      if (timer) clearTimeout(timer);
      ws?.close();
      setWsConnected(false);
    };
  }, [authStatus]);

  const activeMission = activeMissionId
    ? missions[activeMissionId] ?? null
    : null;

  const value: MissionRuntimeState = {
    missions,
    activeMissionId,
    setActiveMissionId: selectMission,
    activeMission,
    logs,
    setLogs,
    wsConnected,
    refreshSummary,
  };

  return (
    <MissionRuntimeContext.Provider value={value}>
      {children}
    </MissionRuntimeContext.Provider>
  );
}
