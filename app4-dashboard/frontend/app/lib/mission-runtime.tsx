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
import {
  autoSelectMission,
  catalogueWithSelectedDetail,
  missionSummaryFromDetail,
  summaryLogMessages,
} from "./mission-runtime-state";
import { parseStatusPayload } from "./mission-api-contracts";
import type {
  MissionLog,
  MissionSummary,
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

export function MissionRuntimeProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const { authStatus, authPrincipal } = useAuth();
  return <AuthenticatedMissionRuntime key={JSON.stringify([authStatus, authPrincipal])}>{children}</AuthenticatedMissionRuntime>;
}

function AuthenticatedMissionRuntime({ children }: { children: React.ReactNode }) {
  const { authStatus } = useAuth();
  const [missions, setMissions] = useState<Record<string, MissionSummary>>({});
  const [activeMissionId, setActiveMissionId] = useState<string | null>(null);
  const [logs, setLogs] = useState<string[]>([]);
  const [wsConnected, setWsConnected] = useState(false);
  const missionsRef = useRef<Record<string, MissionSummary>>({});
  const activeVolIdRef = useRef<string | null>(null);
  const detailRequestRef = useRef(0);
  const catalogRequestRef = useRef(0);
  const lifetime = useRef<AbortController | null>(null);
  useEffect(() => {
    lifetime.current = new AbortController();
    return () => {
      lifetime.current?.abort();
    };
  }, []);

  const refreshSelectedMission = useCallback(async (volId: string) => {
    const request = ++detailRequestRef.current;
    try {
      const detail = await fetchMissionDetail(volId, lifetime.current?.signal);
      if (
        lifetime.current?.signal.aborted ||
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
    const request = ++catalogRequestRef.current;
    try {
      const catalog = await fetchMissionCatalog(100, 0, lifetime.current?.signal);
      while (catalog.items.length < catalog.total) {
        if (lifetime.current?.signal.aborted || request !== catalogRequestRef.current) return;
        const page = await fetchMissionCatalog(100, catalog.items.length, lifetime.current?.signal);
        if (!page.items.length) break;
        catalog.items.push(...page.items);
      }
      if (lifetime.current?.signal.aborted || request !== catalogRequestRef.current) return;
      const current = activeVolIdRef.current;
      const map = catalogueWithSelectedDetail(catalog.items, missionsRef.current, current);
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
    const interval = window.setInterval(() => {
      if (document.visibilityState !== "hidden") void refreshSummary();
    }, 30_000);
    const visible = () => { if (document.visibilityState === "visible") void refreshSummary(); };
    document.addEventListener("visibilitychange", visible);
    return () => {
      window.clearTimeout(initialLoad);
      window.clearInterval(interval);
      document.removeEventListener("visibilitychange", visible);
    };
  }, [authStatus, refreshSummary]);

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
          const payload = parseStatusPayload(JSON.parse(event.data));
          const existing = missionsRef.current[payload.vol_id];
          if (!existing || existing.stage_runs?.length) {
            if (activeVolIdRef.current === payload.vol_id) void refreshSelectedMission(payload.vol_id);
            return;
          }
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
  }, [authStatus, refreshSelectedMission]);

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
