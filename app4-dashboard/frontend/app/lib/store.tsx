"use client";

import React, { createContext, useCallback, useContext, useEffect, useId, useRef, useState } from "react";
import type {
  AIBackend, DatasetItem, MissionLog, MissionSummary, ParameterConfigResponse,
  ParamValue, PipelineName, PodState, StatusPayload, YOLOModelVariant,
  PhaseId,
} from "./types";
import { SERVICE_ORDER } from "./types";
import { fetchBrowse, fetchParameters, fetchPods, fetchSummary, getWsBaseUrl } from "./api";

const DEFAULT_BROWSER_PATH = "datasets/";

type StoreState = {
  // Dataset browser
  currentPath: string;
  items: DatasetItem[];
  selectedPath: string;
  browse: (path: string) => void;
  setSelectedPath: (path: string) => void;

  // Mission
  volId: string;
  setVolId: (id: string) => void;
  missions: Record<string, MissionSummary>;
  activeMissionId: string | null;
  setActiveMissionId: (id: string | null) => void;
  activeMission: MissionSummary | null;
  logs: string[];
  setLogs: React.Dispatch<React.SetStateAction<string[]>>;
  wsConnected: boolean;

  // Pipeline params
  pipeline: PipelineName;
  setPipeline: (p: PipelineName) => void;
  parameterSchema: ParameterConfigResponse | null;
  parameterValues: Record<string, ParamValue>;
  setParameterValues: React.Dispatch<React.SetStateAction<Record<string, ParamValue>>>;
  updateParameter: (key: string, value: ParamValue) => void;
  workDrive: string;
  setWorkDrive: (d: string) => void;

  // AI config
  aiConfidence: number;
  setAiConfidence: (c: number) => void;
  aiBackend: AIBackend;
  setAiBackend: (b: AIBackend) => void;
  aiModelVariant: YOLOModelVariant;
  setAiModelVariant: (m: YOLOModelVariant) => void;
  samPrompt: string;
  setSamPrompt: (p: string) => void;
  selectedClasses: string[];
  setSelectedClasses: (c: string[]) => void;

  // Upload
  uploadDatasetName: string;
  setUploadDatasetName: (n: string) => void;
  uploadFiles: FileList | null;
  setUploadFiles: (f: FileList | null) => void;
  uploadProgress: { total: number; completed: number; failed: number; status: string } | null;
  setUploadProgress: React.Dispatch<React.SetStateAction<{ total: number; completed: number; failed: number; status: string } | null>>;
  isUploading: boolean;
  setIsUploading: (u: boolean) => void;

  // Pods
  pods: PodState[];
  podsError: string | null;

  // Active tab (phase)
  activePhase: PhaseId;
  setActivePhase: (phase: PhaseId) => void;

  // Refresh
  refreshSummary: () => void;
};

const StoreContext = createContext<StoreState | null>(null);

export function useStore() {
  const ctx = useContext(StoreContext);
  if (!ctx) throw new Error("useStore must be used within StoreProvider");
  return ctx;
}

const autoSelectMission = (
  map: Record<string, MissionSummary>,
  preferred?: string | null,
): string | null => {
  // If the preferred mission still exists, keep it
  if (preferred && map[preferred]) return preferred;
  // Otherwise pick the most recently updated running mission, or the most recent overall
  const sorted = Object.values(map).sort((a, b) => b.updated_at - a.updated_at);
  const running = sorted.find((m) => m.overall_status === "processing");
  return running?.vol_id ?? sorted[0]?.vol_id ?? null;
};

export function StoreProvider({ children }: { children: React.ReactNode }) {
  const generatedMissionId = useId().replace(/[^A-Za-z0-9]/g, "") || "new";
  const [currentPath, setCurrentPath] = useState(DEFAULT_BROWSER_PATH);
  const [items, setItems] = useState<DatasetItem[]>([]);
  const [selectedPath, setSelectedPath] = useState("");
  const [volId, setVolId] = useState(`mission-${generatedMissionId}`);
  const [missions, setMissions] = useState<Record<string, MissionSummary>>({});
  const [activeMissionId, setActiveMissionId] = useState<string | null>(null);
  const [logs, setLogs] = useState<string[]>([]);
  const [pods, setPods] = useState<PodState[]>([]);
  const [podsError, setPodsError] = useState<string | null>(null);
  const [wsConnected, setWsConnected] = useState(false);
  const [activePhase, setActivePhase] = useState<PhaseId>("setup");

  const [aiConfidence, setAiConfidence] = useState(0.5);
  const [aiBackend, setAiBackend] = useState<AIBackend>("yolo");
  const [aiModelVariant, setAiModelVariant] = useState<YOLOModelVariant>("yolo26l");
  const [samPrompt, setSamPrompt] = useState("car");
  const [selectedClasses, setSelectedClasses] = useState<string[]>(["car"]);

  const [pipeline, setPipelineRaw] = useState<PipelineName>("modern");
  const [parameterSchema, setParameterSchema] = useState<ParameterConfigResponse | null>(null);
  const [parameterValues, setParameterValues] = useState<Record<string, ParamValue>>({});
  const [workDrive, setWorkDrive] = useState<string>("");

  const [uploadDatasetName, setUploadDatasetName] = useState("");
  const [uploadFiles, setUploadFiles] = useState<FileList | null>(null);
  const [uploadProgress, setUploadProgress] = useState<{ total: number; completed: number; failed: number; status: string } | null>(null);
  const [isUploading, setIsUploading] = useState(false);

  const activeVolIdRef = useRef<string | null>(null);
  const userSelectedRef = useRef(false);

  const setActiveMissionIdUser = useCallback((id: string | null) => {
    userSelectedRef.current = id !== null;
    activeVolIdRef.current = id;
    setActiveMissionId(id);
  }, []);

  const activeMission = activeMissionId ? missions[activeMissionId] ?? null : null;

  const browse = useCallback(async (path: string) => {
    try {
      const data = await fetchBrowse(path);
      if (!Array.isArray(data)) return;
      setItems(data as DatasetItem[]);
      setCurrentPath(path);
    } catch (e) { console.error("Browse error:", e); }
  }, []);

  const refreshSummary = useCallback(async () => {
    try {
      const data = await fetchSummary();
      const map: Record<string, MissionSummary> = {};
      for (const m of (data.missions ?? []) as MissionSummary[]) map[m.vol_id] = m;
      setMissions(map);
      // If the user explicitly picked a mission that still exists, keep it
      const current = activeVolIdRef.current;
      if (userSelectedRef.current && current && map[current]) {
        if (map[current]) setLogs(map[current].logs.map((e) => e.message).slice(-100));
        return;
      }
      const hint = (data.active_vol_id as string) ?? current;
      const next = autoSelectMission(map, hint);
      setActiveMissionId(next);
      activeVolIdRef.current = next;
      if (next && map[next]) setLogs(map[next].logs.map((e) => e.message).slice(-100));
    } catch (e) { console.error("Summary error:", e); }
  }, []);

  const refreshPodsData = useCallback(async () => {
    try {
      const data = await fetchPods();
      setPods((data.pods ?? []) as PodState[]);
      setPodsError((data.error as string) ?? null);
    } catch (e) { setPodsError(String(e)); }
  }, []);

  const loadParameters = useCallback(async () => {
    try {
      const data = (await fetchParameters()) as ParameterConfigResponse;
      setParameterSchema(data);
      setParameterValues(data.pipelines["modern"] ?? {});
      if (data.work_drive_default) setWorkDrive(data.work_drive_default);
    } catch (e) { console.error("Param error:", e); }
  }, []);

  const setPipeline = useCallback((p: PipelineName) => {
    setPipelineRaw(p);
    if (parameterSchema) setParameterValues(parameterSchema.pipelines[p] ?? {});
  }, [parameterSchema]);

  const updateParameter = useCallback((key: string, value: ParamValue) => {
    setParameterValues((prev) => ({ ...prev, [key]: value }));
  }, []);

  // Init
  useEffect(() => {
    const initialLoad = window.setTimeout(() => {
      void browse(DEFAULT_BROWSER_PATH);
      void loadParameters();
      void refreshSummary();
      void refreshPodsData();
    }, 0);
    const si = setInterval(() => void refreshSummary(), 5000);
    const pi = setInterval(() => void refreshPodsData(), 10000);
    return () => {
      window.clearTimeout(initialLoad);
      clearInterval(si);
      clearInterval(pi);
    };
  }, [browse, loadParameters, refreshSummary, refreshPodsData]);

  useEffect(() => { activeVolIdRef.current = activeMissionId; }, [activeMissionId]);

  // WebSocket
  useEffect(() => {
    let ws: WebSocket | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let closed = false;

    const connect = () => {
      ws = new WebSocket(`${getWsBaseUrl()}/ws/status`);
      ws.onopen = () => setWsConnected(true);
      ws.onmessage = (ev) => {
        try {
          const d = JSON.parse(ev.data) as StatusPayload;
          if (!d.vol_id) return;
          setMissions((prev) => {
            const existing = prev[d.vol_id] ?? {
              vol_id: d.vol_id, services: {}, logs: [], updated_at: Date.now() / 1000, overall_status: "processing",
            };
            const svc = { ...existing.services, ...(d.service ? { [d.service]: d } : {}) };
            const newLogs = d.log
              ? [...existing.logs.slice(-199), { message: d.log, service: d.service, step: d.step, status: d.status, ts: Date.now() / 1000 } as MissionLog]
              : existing.logs;
            const mission: MissionSummary = {
              ...existing, services: svc, logs: newLogs, updated_at: Date.now() / 1000,
              overall_status: d.status === "error" ? "error"
                : Object.values(svc).length > 0 && (SERVICE_ORDER as string[]).every((s) => (svc as Record<string, StatusPayload>)[s]?.status === "success") ? "success"
                : "processing",
            };
            const next = { ...prev, [d.vol_id]: mission };
            // Only auto-switch if user hasn't explicitly selected a mission
            if (!userSelectedRef.current) {
              const sel = autoSelectMission(next, activeVolIdRef.current ?? d.vol_id);
              if (sel !== activeVolIdRef.current) { activeVolIdRef.current = sel; setActiveMissionId(sel); }
            }
            if ((activeVolIdRef.current ?? d.vol_id) === d.vol_id && d.log)
              setLogs(mission.logs.map((e) => e.message).slice(-100));
            return next;
          });
        } catch (e) { console.error("WS parse error:", e); }
      };
      ws.onclose = () => { setWsConnected(false); if (!closed) timer = setTimeout(connect, 1000); };
      ws.onerror = (e) => console.error("WS error:", e);
    };
    connect();
    return () => { closed = true; if (timer) clearTimeout(timer); ws?.close(); };
  }, []);

  const value: StoreState = {
    currentPath, items, selectedPath, browse, setSelectedPath,
    volId, setVolId, missions, activeMissionId, setActiveMissionId: setActiveMissionIdUser, activeMission, logs, setLogs, wsConnected,
    pipeline, setPipeline, parameterSchema, parameterValues, setParameterValues, updateParameter,
    workDrive, setWorkDrive,
    aiConfidence, setAiConfidence, aiBackend, setAiBackend, aiModelVariant, setAiModelVariant,
    samPrompt, setSamPrompt, selectedClasses, setSelectedClasses,
    uploadDatasetName, setUploadDatasetName, uploadFiles, setUploadFiles,
    uploadProgress, setUploadProgress, isUploading, setIsUploading,
    pods, podsError, activePhase, setActivePhase, refreshSummary,
  };

  return <StoreContext.Provider value={value}>{children}</StoreContext.Provider>;
}
