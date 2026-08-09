"use client";

import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import { fetchSummary, getWsBaseUrl } from "./api";
import { useAuth } from "./auth";
import type { MissionLog, MissionSummary, StatusPayload } from "./types";
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

export const summaryLogMessages = (
  mission: MissionSummary,
): string[] | null => {
  const messages = mission.logs.map((entry) => entry.message).slice(-100);
  return messages.length > 0 ? messages : null;
};

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
  const activeVolIdRef = useRef<string | null>(null);
  const userSelectedRef = useRef(false);

  const setActiveMissionIdUser = useCallback((id: string | null) => {
    userSelectedRef.current = id !== null;
    activeVolIdRef.current = id;
    setActiveMissionId(id);
  }, []);

  const activeMission = activeMissionId
    ? missions[activeMissionId] ?? null
    : null;

  const refreshSummary = useCallback(async () => {
    try {
      const data = await fetchSummary();
      const map: Record<string, MissionSummary> = {};
      for (const mission of (data.missions ?? []) as MissionSummary[]) {
        map[mission.vol_id] = mission;
      }
      setMissions((previous) => mergeMissionSnapshots(previous, map));
      const current = activeVolIdRef.current;
      if (userSelectedRef.current && current && map[current]) {
        const summaryLogs = summaryLogMessages(map[current]);
        if (summaryLogs) setLogs(summaryLogs);
        return;
      }
      const hint = (data.active_vol_id as string) ?? current;
      const next = autoSelectMission(map, hint);
      setActiveMissionId(next);
      activeVolIdRef.current = next;
      if (next && map[next]) {
        const summaryLogs = summaryLogMessages(map[next]);
        if (summaryLogs) setLogs(summaryLogs);
      }
    } catch (error) {
      console.error("Summary error:", error);
    }
  }, []);

  useEffect(() => {
    if (authStatus !== "authenticated") return;
    const initialLoad = window.setTimeout(() => void refreshSummary(), 0);
    const interval = window.setInterval(() => void refreshSummary(), 5000);
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
          setMissions((previous) => {
            const existing = previous[payload.vol_id] ?? {
              vol_id: payload.vol_id,
              services: {},
              logs: [],
              updated_at: Date.now() / 1000,
              overall_status: "processing",
            };
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
            const next = { ...previous, [payload.vol_id]: mission };
            if (!userSelectedRef.current) {
              const selected = autoSelectMission(
                next,
                activeVolIdRef.current ?? payload.vol_id,
              );
              if (selected !== activeVolIdRef.current) {
                activeVolIdRef.current = selected;
                setActiveMissionId(selected);
              }
            }
            if (
              (activeVolIdRef.current ?? payload.vol_id) === payload.vol_id &&
              payload.log
            ) {
              setLogs(mission.logs.map((entry) => entry.message).slice(-100));
            }
            return next;
          });
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

  const value: MissionRuntimeState = {
    missions,
    activeMissionId,
    setActiveMissionId: setActiveMissionIdUser,
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
