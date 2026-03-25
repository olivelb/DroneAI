"use client";

import React, { useEffect, useRef, useState } from "react";
import {
  Activity,
  AlertCircle,
  CheckCircle,
  ChevronRight,
  Cpu,
  File,
  Folder,
  Home,
  Map as MapIcon,
  Play,
  Settings,
  Terminal,
  Trash2,
  Zap,
} from "lucide-react";

type ServiceName = "COLMAP" | "TILER" | "IA";
type PipelineName = "modern" | "legacy";
type AIBackend = "yolo" | "sam3";
type ParamValue = string | boolean;

type StatusPayload = {
  vol_id: string;
  step?: string;
  progress?: number;
  status?: string;
  service?: string;
  log?: string;
};

type MissionLog = {
  service?: string;
  step?: string;
  status?: string;
  message: string;
  ts?: number;
};

type MissionSummary = {
  vol_id: string;
  services: Record<string, StatusPayload>;
  logs: MissionLog[];
  updated_at: number;
  overall_status: string;
};

type DatasetItem = {
  name: string;
  path: string;
  is_dir: boolean;
  image_count: number;
};

type PodState = {
  name: string;
  phase: string;
  ready: string | null;
  restarts: number | null;
  reason?: string | null;
  last_terminated_reason?: string | null;
  last_terminated_exit_code?: number | null;
  oom_killed?: boolean;
  memory_limit?: string | null;
  memory_request?: string | null;
};

type ParameterMeta = {
  label: string;
  type: "select" | "int" | "float" | "bool" | "text";
  group: string;
  min?: number;
  max?: number;
  step?: number;
  options?: string[];
};

type ParameterConfigResponse = {
  pipelines: Record<PipelineName, Record<string, ParamValue>>;
  metadata: Record<string, ParameterMeta>;
};

type ResourceSummary = {
  memory: {
    total_bytes: number;
    available_bytes: number;
    free_bytes: number;
    total_gib: number;
    available_gib: number;
    free_gib: number;
  };
};

type EstimateResponse = {
  pipeline: string;
  params: Record<string, ParamValue>;
  dataset: {
    path: string;
    image_count: number;
    sampled_images: Array<{
      name: string;
      width: number;
      height: number;
      scaled_width: number;
      scaled_height: number;
      pixels: number;
      fusion_bytes: number;
    }>;
  };
  memory: {
    formula: string;
    cache_size_gib: number;
    cache_size_bytes: number;
    total_fusion_bytes: number;
    total_fusion_gib: number;
    largest_image_bytes: number;
    largest_image_gib: number;
    images_fit_in_cache: number;
    target_cached_images: number;
    available_ram_gib: number;
    safe_cache_gib: number;
  };
  recommendation: {
    fusion_cache_size: number;
    fusion_max_image_size: number;
    reason: string;
  };
};

const SERVICE_ORDER: ServiceName[] = ["COLMAP", "TILER", "IA"];
const AVAILABLE_CLASSES = ["person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat"];
const AVAILABLE_AI_BACKENDS: Array<{ value: AIBackend; label: string; description: string }> = [
  { value: "yolo", label: "YOLO OBB", description: "Fast oriented-box vehicle detector" },
  { value: "sam3", label: "SAM 3", description: "Prompted mask segmentation from Meta" },
];

const getApiBaseUrl = () => {
  if (typeof window === "undefined") {
    return "http://localhost:30080";
  }
  return `http://${window.location.hostname}:30080`;
};

const getWsBaseUrl = () => {
  if (typeof window === "undefined") {
    return "ws://localhost:30080";
  }
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${window.location.hostname}:30080`;
};

const isMissionTerminal = (mission?: MissionSummary | null) => {
  if (!mission) {
    return false;
  }
  return mission.overall_status === "success" || mission.overall_status === "error";
};

const formatBytesToGib = (bytes?: number | null) => {
  if (!bytes) {
    return "0.00 GiB";
  }
  return `${(bytes / 1024 ** 3).toFixed(2)} GiB`;
};

const formatPodPhase = (pod: PodState) => {
  const phase = pod.phase || "unknown";
  if (pod.oom_killed) {
    return `${phase} (oomkilled)`;
  }
  if (pod.reason) {
    return `${phase} (${pod.reason})`;
  }
  return phase;
};

export default function Dashboard() {
  const [currentPath, setCurrentPath] = useState("/host/mnt/j");
  const [items, setItems] = useState<DatasetItem[]>([]);
  const [selectedPath, setSelectedPath] = useState("");
  const [workspacePath, setWorkspacePath] = useState("/mnt/j/workspace");
  const [volId, setVolId] = useState(`vol_${Math.floor(Math.random() * 1000)}`);
  const [missions, setMissions] = useState<Record<string, MissionSummary>>({});
  const [activeMissionId, setActiveMissionId] = useState<string | null>(null);
  const [logs, setLogs] = useState<string[]>([]);
  const [pods, setPods] = useState<PodState[]>([]);
  const [podsError, setPodsError] = useState<string | null>(null);
  const [wsConnected, setWsConnected] = useState(false);
  const [activeTab, setActiveTab] = useState("control");
  const [aiConfidence, setAiConfidence] = useState(0.5);
  const [aiBackend, setAiBackend] = useState<AIBackend>("yolo");
  const [samPrompt, setSamPrompt] = useState("car");
  const [selectedClasses, setSelectedClasses] = useState<string[]>(["car"]);
  const [pipeline, setPipeline] = useState<PipelineName>("modern");
  const [parameterSchema, setParameterSchema] = useState<ParameterConfigResponse | null>(null);
  const [parameterValues, setParameterValues] = useState<Record<string, ParamValue>>({});
  const [estimate, setEstimate] = useState<EstimateResponse | null>(null);
  const [estimateError, setEstimateError] = useState<string | null>(null);
  const [resources, setResources] = useState<ResourceSummary | null>(null);

  const logContainerRef = useRef<HTMLDivElement>(null);

  const progress = activeMissionId ? missions[activeMissionId]?.services ?? {} : {};
  const activeMission = activeMissionId ? missions[activeMissionId] : null;
  const parameterMetadata = parameterSchema?.metadata ?? {};
  const parameterGroups = Object.entries(parameterMetadata).reduce<Record<string, string[]>>((acc, [key, meta]) => {
    if (!acc[meta.group]) {
      acc[meta.group] = [];
    }
    acc[meta.group].push(key);
    return acc;
  }, {});

  const syncMissionSelection = (
    missionMap: Record<string, MissionSummary>,
    preferredMissionId?: string | null,
  ): string | null => {
    const preferred = preferredMissionId ? missionMap[preferredMissionId] : undefined;
    if (preferred && !isMissionTerminal(preferred)) {
      return preferredMissionId ?? null;
    }
    const runningMission = Object.values(missionMap)
      .filter((mission) => mission.overall_status === "processing")
      .sort((left, right) => right.updated_at - left.updated_at)[0];
    if (runningMission) {
      return runningMission.vol_id;
    }
    return null;
  };

  const browse = async (path: string) => {
    try {
      const res = await fetch(`${getApiBaseUrl()}/browse?path=${encodeURIComponent(path)}`);
      const data = await res.json();
      if (data.error) {
        console.error(data.error);
        return;
      }
      setItems(data);
      setCurrentPath(path);
    } catch (error) {
      console.error("Browse error:", error);
    }
  };

  const refreshSummary = async () => {
    try {
      const res = await fetch(`${getApiBaseUrl()}/status/summary`, { cache: "no-store" });
      const data = await res.json();
      const missionMap: Record<string, MissionSummary> = {};
      for (const mission of data.missions ?? []) {
        missionMap[mission.vol_id] = mission;
      }
      setMissions(missionMap);
      const nextActiveMission = syncMissionSelection(missionMap, data.active_vol_id ?? activeMissionId);
      setActiveMissionId(nextActiveMission);
      if (nextActiveMission && missionMap[nextActiveMission]) {
        setVolId(nextActiveMission);
        setLogs(missionMap[nextActiveMission].logs.map((entry) => entry.message).slice(-100));
      }
    } catch (error) {
      console.error("Summary refresh error:", error);
    }
  };

  const refreshPods = async () => {
    try {
      const res = await fetch(`${getApiBaseUrl()}/pods`, { cache: "no-store" });
      const data = await res.json();
      setPods(data.pods ?? []);
      setPodsError(data.error ?? null);
    } catch (error) {
      console.error("Pod refresh error:", error);
      setPodsError(String(error));
    }
  };

  const refreshResources = async () => {
    try {
      const res = await fetch(`${getApiBaseUrl()}/system/resources`, { cache: "no-store" });
      const data = await res.json();
      setResources(data);
    } catch (error) {
      console.error("Resource refresh error:", error);
    }
  };

  const fetchParameters = async (nextPipeline: PipelineName) => {
    try {
      const res = await fetch(`${getApiBaseUrl()}/mission/parameters`, { cache: "no-store" });
      const data = (await res.json()) as ParameterConfigResponse;
      setParameterSchema(data);
      setParameterValues(data.pipelines[nextPipeline] ?? {});
    } catch (error) {
      console.error("Parameter fetch error:", error);
    }
  };

  const requestEstimate = async (
    inputDir: string,
    nextPipeline: PipelineName,
    nextParams: Record<string, ParamValue>,
  ) => {
    if (!inputDir) {
      setEstimate(null);
      setEstimateError(null);
      return;
    }
    try {
      const res = await fetch(`${getApiBaseUrl()}/mission/estimate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          input_dir: inputDir,
          pipeline: nextPipeline,
          colmap_params: nextParams,
        }),
      });
      const data = (await res.json()) as EstimateResponse;
      setEstimate(data);
      setEstimateError(null);
    } catch (error) {
      console.error("Estimate error:", error);
      setEstimateError(String(error));
    }
  };

  useEffect(() => {
    void browse(currentPath);
    void fetchParameters(pipeline);
    void refreshSummary();
    void refreshPods();
    void refreshResources();

    const summaryInterval = setInterval(() => {
      void refreshSummary();
    }, 5000);
    const podInterval = setInterval(() => {
      void refreshPods();
      void refreshResources();
    }, 10000);

    return () => {
      clearInterval(summaryInterval);
      clearInterval(podInterval);
    };
  }, []);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let closedByUser = false;

    const connect = () => {
      ws = new WebSocket(`${getWsBaseUrl()}/ws/status`);

      ws.onopen = () => {
        setWsConnected(true);
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data) as StatusPayload;
          if (!data.vol_id) {
            return;
          }
          setMissions((prev) => {
            const existing = prev[data.vol_id] ?? {
              vol_id: data.vol_id,
              services: {},
              logs: [],
              updated_at: Date.now() / 1000,
              overall_status: "processing",
            };
            const nextServices = {
              ...existing.services,
              ...(data.service ? { [data.service]: data } : {}),
            };
            const nextLogs = data.log
              ? [...existing.logs.slice(-199), { message: data.log, service: data.service, step: data.step, status: data.status, ts: Date.now() / 1000 }]
              : existing.logs;
            const nextMission: MissionSummary = {
              ...existing,
              services: nextServices,
              logs: nextLogs,
              updated_at: Date.now() / 1000,
              overall_status: data.status === "error"
                ? "error"
                : Object.values(nextServices).length > 0 && SERVICE_ORDER.every((service) => nextServices[service]?.status === "success")
                  ? "success"
                  : "processing",
            };
            const nextMissions = {
              ...prev,
              [data.vol_id]: nextMission,
            };
            const nextActiveMissionId = syncMissionSelection(nextMissions, activeMissionId ?? data.vol_id);
            if (nextActiveMissionId !== activeMissionId) {
              setActiveMissionId(nextActiveMissionId);
              if (nextActiveMissionId) {
                setVolId(nextActiveMissionId);
              } else if (activeMissionId) {
                setVolId(`vol_${Math.floor(Math.random() * 1000)}`);
              }
            }
            if ((activeMissionId ?? data.vol_id) === data.vol_id && data.log) {
              setLogs(nextMission.logs.map((entry) => entry.message).slice(-100));
            }
            return nextMissions;
          });
        } catch (error) {
          console.error("WebSocket message parse error:", error);
        }
      };

      ws.onclose = () => {
        setWsConnected(false);
        if (!closedByUser) {
          reconnectTimer = setTimeout(connect, 1000);
        }
      };

      ws.onerror = (error) => {
        console.error("WebSocket error:", error);
      };
    };

    connect();

    return () => {
      closedByUser = true;
      if (reconnectTimer) {
        clearTimeout(reconnectTimer);
      }
      if (ws) {
        ws.close();
      }
    };
  }, [activeMissionId]);

  useEffect(() => {
    if (logContainerRef.current) {
      logContainerRef.current.scrollTop = logContainerRef.current.scrollHeight;
    }
  }, [logs]);

  useEffect(() => {
    if (!parameterSchema) {
      return;
    }
    setParameterValues(parameterSchema.pipelines[pipeline] ?? {});
  }, [pipeline, parameterSchema]);

  useEffect(() => {
    const timer = setTimeout(() => {
      void requestEstimate(selectedPath, pipeline, parameterValues);
    }, 350);

    return () => clearTimeout(timer);
  }, [selectedPath, pipeline, parameterValues]);

  const updateParameter = (key: string, value: ParamValue) => {
    setParameterValues((prev) => ({
      ...prev,
      [key]: value,
    }));
  };

  const applyRecommendation = () => {
    if (!estimate) {
      return;
    }
    setParameterValues((prev) => ({
      ...prev,
      fusion_cache_size: String(estimate.recommendation.fusion_cache_size),
      fusion_max_image_size: String(estimate.recommendation.fusion_max_image_size),
    }));
  };

  const startPipeline = async () => {
    setLogs(["[SYSTEM] Starting pipeline..."]);
    setActiveMissionId(volId);
    const normalizedPrompt = samPrompt.trim() || "car";
    const params = {
      vol_id: volId,
      input_dir: selectedPath,
      workspace_dir: workspacePath,
      epsg: "EPSG:4326",
      camera_model: "PINHOLE",
      pipeline,
      tile_size: 1024,
      ai_confidence: aiConfidence,
      ai_backend: aiBackend,
      sam_prompt: normalizedPrompt,
      classes: aiBackend === "sam3" ? [normalizedPrompt] : selectedClasses,
      colmap_params: parameterValues,
    };

    try {
      const res = await fetch(`${getApiBaseUrl()}/mission`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(params),
      });
      const result = await res.json();
      console.log("Mission started:", result);
      setLogs((prev) => [...prev, `[SYSTEM] Mission ${volId} sent successfully.`]);
    } catch (error) {
      setLogs((prev) => [...prev, `[SYSTEM] Error starting mission: ${error}`]);
    }
  };

  const cancelPipeline = async () => {
    try {
      const targetMissionId = activeMissionId ?? volId;
      const res = await fetch(`${getApiBaseUrl()}/mission/cancel?vol_id=${encodeURIComponent(targetMissionId)}`, {
        method: "POST",
      });
      const result = await res.json();
      setLogs((prev) => [...prev, `[SYSTEM] Cancel command sent: ${result.message}`]);
    } catch (error) {
      setLogs((prev) => [...prev, `[SYSTEM] Error canceling mission: ${error}`]);
    }
  };

  const goUp = () => {
    const parts = currentPath.split("/").filter(Boolean);
    parts.pop();
    void browse("/" + parts.join("/"));
  };

  const renderParameterField = (key: string) => {
    const meta = parameterMetadata[key];
    const value = parameterValues[key] ?? "";
    if (!meta) {
      return null;
    }

    if (meta.type === "bool") {
      const checked = Boolean(value);
      return (
        <button
          key={key}
          onClick={() => updateParameter(key, !checked)}
          className={`flex items-center justify-between rounded-2xl border px-4 py-3 text-left transition ${checked ? "border-emerald-400 bg-emerald-500/10" : "border-slate-700 bg-slate-950/60"}`}
        >
          <div>
            <div className="text-xs font-bold text-slate-100">{meta.label}</div>
            <div className="text-[10px] uppercase tracking-[0.2em] text-slate-500">{meta.group}</div>
          </div>
          <div className={`rounded-full px-3 py-1 text-[10px] font-black uppercase ${checked ? "bg-emerald-500 text-black" : "bg-slate-800 text-slate-400"}`}>
            {checked ? "On" : "Off"}
          </div>
        </button>
      );
    }

    if (meta.type === "select") {
      return (
        <label key={key} className="block rounded-2xl border border-slate-700 bg-slate-950/60 p-4">
          <div className="mb-2 text-xs font-bold text-slate-100">{meta.label}</div>
          <select
            value={String(value)}
            onChange={(event) => updateParameter(key, event.target.value)}
            className="w-full rounded-xl border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-100 outline-none focus:border-blue-400"
          >
            {meta.options?.map((option) => (
              <option key={option} value={option}>{option}</option>
            ))}
          </select>
        </label>
      );
    }

    return (
      <label key={key} className="block rounded-2xl border border-slate-700 bg-slate-950/60 p-4">
        <div className="mb-2 text-xs font-bold text-slate-100">{meta.label}</div>
        <input
          type={meta.type === "text" ? "text" : "number"}
          min={meta.min}
          max={meta.max}
          step={meta.step}
          value={String(value)}
          onChange={(event) => updateParameter(key, event.target.value)}
          className="w-full rounded-xl border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-100 outline-none focus:border-blue-400"
        />
      </label>
    );
  };

  return (
    <div className="min-h-screen bg-[radial-gradient(circle_at_top_left,_rgba(34,197,94,0.18),_transparent_30%),radial-gradient(circle_at_top_right,_rgba(59,130,246,0.16),_transparent_28%),linear-gradient(180deg,_#07111f_0%,_#0f172a_42%,_#111827_100%)] p-6 text-slate-100">
      <div className="mx-auto flex max-w-[1680px] flex-col gap-6">
        <header className="flex flex-wrap items-center justify-between gap-4 border-b border-slate-700/70 pb-4">
          <div>
            <h1 className="text-3xl font-black tracking-tight text-white">DroneAI Control Center</h1>
            <p className="mt-1 text-xs font-semibold uppercase tracking-[0.28em] text-slate-400">Photogrammetry, fusion diagnostics, and live tuning</p>
          </div>
          <div className="flex gap-3">
            <button
              onClick={() => setActiveTab("control")}
              className={`rounded-2xl px-4 py-2 text-sm font-black transition ${activeTab === "control" ? "bg-blue-500 text-white shadow-[0_12px_30px_rgba(59,130,246,0.25)]" : "bg-slate-900/70 text-slate-400 hover:text-white"}`}
            >
              Mission Control
            </button>
            <button
              onClick={() => setActiveTab("map")}
              className={`rounded-2xl px-4 py-2 text-sm font-black transition ${activeTab === "map" ? "bg-blue-500 text-white shadow-[0_12px_30px_rgba(59,130,246,0.25)]" : "bg-slate-900/70 text-slate-400 hover:text-white"}`}
            >
              Data Results
            </button>
          </div>
        </header>

        {activeTab === "control" ? (
          <div className="grid grid-cols-12 gap-6">
            <section className="col-span-12 lg:col-span-3 overflow-hidden rounded-[28px] border border-slate-700/70 bg-slate-900/70 backdrop-blur">
              <div className="border-b border-slate-700/60 p-5">
                <h2 className="flex items-center gap-2 text-lg font-black text-emerald-300"><Folder size={20} /> Dataset Browser</h2>
                <div className="mt-3 flex items-center gap-2 rounded-2xl border border-slate-700 bg-slate-950/80 p-2 text-xs font-mono text-slate-400">
                  <button onClick={() => void browse("/")} className="rounded-lg p-1 hover:text-white"><Home size={14} /></button>
                  <ChevronRight size={12} className="text-slate-600" />
                  <span className="truncate">{currentPath}</span>
                  <button onClick={goUp} className="ml-auto rounded-lg bg-slate-800 px-2 py-1 text-[10px] font-black uppercase tracking-[0.2em] text-slate-200">Up</button>
                </div>
              </div>
              <div className="max-h-[620px] overflow-y-auto p-3">
                <div className="space-y-2">
                  {items.map((item) => (
                    <div
                      key={item.path}
                      onClick={() => setSelectedPath(item.path)}
                      onDoubleClick={() => item.is_dir ? void browse(item.path) : undefined}
                      className={`flex cursor-pointer items-center gap-3 rounded-2xl border p-3 transition ${selectedPath === item.path ? "border-blue-400 bg-blue-500/10" : "border-transparent bg-slate-950/40 hover:border-slate-600"}`}
                    >
                      <div className="rounded-xl bg-slate-900 p-2">
                        {item.is_dir ? <Folder size={18} className="text-amber-300" /> : <File size={18} className="text-slate-500" />}
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-sm font-bold text-slate-100">{item.name}</div>
                        {item.is_dir && item.image_count > 0 ? (
                          <div className="text-[10px] font-black uppercase tracking-[0.2em] text-emerald-400">{item.image_count} images</div>
                        ) : null}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
              <div className="border-t border-slate-700/60 bg-slate-950/70 p-4">
                <div className="text-[10px] font-black uppercase tracking-[0.25em] text-slate-500">Selected Dataset</div>
                <div className="mt-2 truncate rounded-2xl border border-blue-400/30 bg-blue-500/10 px-3 py-2 font-mono text-xs text-blue-200">{selectedPath || "None selected"}</div>
              </div>
            </section>

            <section className="col-span-12 lg:col-span-6 space-y-6">
              <div className="rounded-[28px] border border-slate-700/70 bg-slate-900/70 p-6 backdrop-blur">
                <div className="flex flex-wrap items-start justify-between gap-4">
                  <div>
                    <h2 className="flex items-center gap-2 text-lg font-black text-blue-300"><Settings size={20} /> Mission Configuration</h2>
                    <p className="mt-1 text-sm text-slate-400">All COLMAP runtime parameters are editable before launch.</p>
                  </div>
                  <div className="flex gap-2">
                    <button
                      onClick={startPipeline}
                      disabled={!selectedPath || !workspacePath}
                      className="rounded-2xl bg-emerald-500 px-5 py-3 text-sm font-black text-black transition hover:bg-emerald-400 disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-400"
                    >
                      <span className="inline-flex items-center gap-2"><Play size={16} fill="currentColor" /> Run Mission</span>
                    </button>
                    <button
                      onClick={cancelPipeline}
                      className="rounded-2xl bg-red-500 px-5 py-3 text-sm font-black text-white transition hover:bg-red-400"
                    >
                      Cancel
                    </button>
                  </div>
                </div>

                <div className="mt-6 grid gap-5 md:grid-cols-2">
                  <label className="block rounded-2xl border border-slate-700 bg-slate-950/70 p-4">
                    <div className="mb-2 text-[10px] font-black uppercase tracking-[0.24em] text-slate-500">Volume ID</div>
                    <input value={volId} onChange={(event) => setVolId(event.target.value)} className="w-full rounded-xl border border-slate-700 bg-slate-900 px-3 py-2 font-mono text-sm outline-none focus:border-blue-400" />
                  </label>
                  <label className="block rounded-2xl border border-slate-700 bg-slate-950/70 p-4">
                    <div className="mb-2 text-[10px] font-black uppercase tracking-[0.24em] text-slate-500">Workspace Directory</div>
                    <input value={workspacePath} onChange={(event) => setWorkspacePath(event.target.value)} className="w-full rounded-xl border border-slate-700 bg-slate-900 px-3 py-2 font-mono text-sm outline-none focus:border-blue-400" />
                  </label>
                </div>

                <div className="mt-5 grid gap-5 md:grid-cols-3">
                  <div className="rounded-2xl border border-cyan-500/30 bg-cyan-500/10 p-4">
                    <div className="mb-3 text-[10px] font-black uppercase tracking-[0.24em] text-cyan-300">Pipeline Profile</div>
                    <div className="grid grid-cols-2 gap-2 rounded-2xl bg-slate-950/70 p-1">
                      <button onClick={() => setPipeline("modern")} className={`rounded-xl px-3 py-2 text-xs font-black transition ${pipeline === "modern" ? "bg-cyan-500 text-slate-950" : "text-slate-400"}`}>Modern</button>
                      <button onClick={() => setPipeline("legacy")} className={`rounded-xl px-3 py-2 text-xs font-black transition ${pipeline === "legacy" ? "bg-amber-500 text-black" : "text-slate-400"}`}>Legacy</button>
                    </div>
                  </div>
                  <div className="rounded-2xl border border-fuchsia-500/30 bg-fuchsia-500/10 p-4">
                    <div className="mb-3 text-[10px] font-black uppercase tracking-[0.24em] text-fuchsia-200">AI Backend</div>
                    <div className="grid gap-2 rounded-2xl bg-slate-950/70 p-1">
                      {AVAILABLE_AI_BACKENDS.map((backend) => (
                        <button
                          key={backend.value}
                          onClick={() => setAiBackend(backend.value)}
                          className={`rounded-xl px-3 py-2 text-left transition ${aiBackend === backend.value ? "bg-fuchsia-400 text-slate-950" : "text-slate-300 hover:bg-slate-900/80"}`}
                        >
                          <div className="text-xs font-black uppercase tracking-[0.16em]">{backend.label}</div>
                          <div className={`text-[10px] ${aiBackend === backend.value ? "text-slate-900/70" : "text-slate-500"}`}>{backend.description}</div>
                        </button>
                      ))}
                    </div>
                  </div>
                  <div className="rounded-2xl border border-blue-500/30 bg-blue-500/10 p-4">
                    <div className="mb-3 flex items-center justify-between text-[10px] font-black uppercase tracking-[0.24em] text-blue-300">
                      <span>AI Confidence</span>
                      <span>{Math.round(aiConfidence * 100)}%</span>
                    </div>
                    <input type="range" min="0.1" max="0.9" step="0.05" value={aiConfidence} onChange={(event) => setAiConfidence(parseFloat(event.target.value))} className="w-full accent-blue-400" />
                  </div>
                </div>

                <div className="mt-5 rounded-2xl border border-slate-700 bg-slate-950/60 p-4">
                  <div className="mb-3 text-[10px] font-black uppercase tracking-[0.24em] text-slate-500">
                    {aiBackend === "sam3" ? "SAM3 Prompt" : "YOLO Classes"}
                  </div>
                  {aiBackend === "sam3" ? (
                    <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_220px]">
                      <label className="block">
                        <input
                          value={samPrompt}
                          onChange={(event) => setSamPrompt(event.target.value)}
                          placeholder="car"
                          className="w-full rounded-xl border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-100 outline-none focus:border-fuchsia-400"
                        />
                      </label>
                      <div className="rounded-xl border border-fuchsia-500/20 bg-fuchsia-500/10 px-3 py-2 text-xs text-fuchsia-100">
                        Prompt SAM 3 with a concept like <span className="font-black">car</span>, <span className="font-black">vehicle</span>, or a more specific phrase.
                      </div>
                    </div>
                  ) : (
                    <div className="flex flex-wrap gap-2">
                      {AVAILABLE_CLASSES.map((cls) => {
                        const selected = selectedClasses.includes(cls);
                        return (
                          <button
                            key={cls}
                            onClick={() => setSelectedClasses((prev) => selected ? prev.filter((entry) => entry !== cls) : [...prev, cls])}
                            className={`rounded-xl border px-3 py-1.5 text-[10px] font-black uppercase tracking-[0.16em] transition ${selected ? "border-blue-300 bg-blue-500 text-white" : "border-slate-700 bg-slate-900 text-slate-400"}`}
                          >
                            {cls}
                          </button>
                        );
                      })}
                    </div>
                  )}
                </div>

                <div className="mt-6 rounded-[24px] border border-emerald-500/30 bg-emerald-500/10 p-5">
                  <div className="flex flex-wrap items-start justify-between gap-4">
                    <div>
                      <h3 className="flex items-center gap-2 text-base font-black text-emerald-200"><Zap size={18} /> Fusion RAM Estimator</h3>
                      <p className="mt-1 text-sm text-emerald-50/75">Computed from the selected dataset dimensions after applying the chosen fusion image cap.</p>
                    </div>
                    <button onClick={applyRecommendation} disabled={!estimate} className="rounded-2xl bg-emerald-300 px-4 py-2 text-xs font-black uppercase tracking-[0.2em] text-black transition disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-400">
                      Apply Recommendation
                    </button>
                  </div>

                  <div className="mt-4 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
                    <div className="rounded-2xl border border-slate-700/60 bg-slate-950/70 p-4">
                      <div className="text-[10px] font-black uppercase tracking-[0.22em] text-slate-500">Available RAM</div>
                      <div className="mt-2 text-2xl font-black text-white">{resources?.memory.available_gib?.toFixed(2) ?? "0.00"} GiB</div>
                      <div className="mt-1 text-xs text-slate-400">Host free-for-work estimate</div>
                    </div>
                    <div className="rounded-2xl border border-slate-700/60 bg-slate-950/70 p-4">
                      <div className="text-[10px] font-black uppercase tracking-[0.22em] text-slate-500">Configured Cache</div>
                      <div className="mt-2 text-2xl font-black text-white">{estimate?.memory.cache_size_gib?.toFixed(2) ?? "0.00"} GiB</div>
                      <div className="mt-1 text-xs text-slate-400">Exact StereoFusion.cache_size reservation</div>
                    </div>
                    <div className="rounded-2xl border border-slate-700/60 bg-slate-950/70 p-4">
                      <div className="text-[10px] font-black uppercase tracking-[0.22em] text-slate-500">Largest Image Working Set</div>
                      <div className="mt-2 text-2xl font-black text-white">{estimate?.memory.largest_image_gib?.toFixed(2) ?? "0.00"} GiB</div>
                      <div className="mt-1 text-xs text-slate-400">{estimate?.memory.formula ?? "pixels x RGB + depth + normal buffers"}</div>
                    </div>
                    <div className="rounded-2xl border border-slate-700/60 bg-slate-950/70 p-4">
                      <div className="text-[10px] font-black uppercase tracking-[0.22em] text-slate-500">Cache Coverage</div>
                      <div className="mt-2 text-2xl font-black text-white">{estimate?.memory.images_fit_in_cache ?? 0}/{estimate?.memory.target_cached_images ?? 8}</div>
                      <div className="mt-1 text-xs text-slate-400">Heaviest scaled images fitting in cache</div>
                    </div>
                  </div>

                  <div className="mt-4 grid gap-4 md:grid-cols-2">
                    <div className="rounded-2xl border border-slate-700/60 bg-slate-950/70 p-4">
                      <div className="text-[10px] font-black uppercase tracking-[0.22em] text-slate-500">Recommended Pair</div>
                      <div className="mt-2 text-sm text-slate-200">fusion_max_image_size = {estimate?.recommendation.fusion_max_image_size ?? "-"}</div>
                      <div className="text-sm text-slate-200">fusion_cache_size = {estimate?.recommendation.fusion_cache_size ?? "-"} GiB</div>
                      <div className="mt-2 text-xs text-slate-400">{estimate?.recommendation.reason ?? "Select a dataset to compute a recommendation."}</div>
                    </div>
                    <div className="rounded-2xl border border-slate-700/60 bg-slate-950/70 p-4">
                      <div className="text-[10px] font-black uppercase tracking-[0.22em] text-slate-500">Dataset Summary</div>
                      <div className="mt-2 text-sm text-slate-200">Images analyzed: {estimate?.dataset.image_count ?? 0}</div>
                      <div className="text-sm text-slate-200">Total scaled fusion bytes: {estimate ? formatBytesToGib(estimate.memory.total_fusion_bytes) : "0.00 GiB"}</div>
                      <div className="text-sm text-slate-200">Safe cache from current free RAM: {estimate?.memory.safe_cache_gib?.toFixed(2) ?? "0.00"} GiB</div>
                    </div>
                  </div>

                  {estimateError ? <div className="mt-3 text-xs text-red-300">Estimator error: {estimateError}</div> : null}
                </div>

                <div className="mt-6 space-y-5">
                  {Object.entries(parameterGroups).map(([group, keys]) => (
                    <div key={group} className="rounded-[24px] border border-slate-700 bg-slate-950/40 p-5">
                      <div className="mb-4 text-[10px] font-black uppercase tracking-[0.26em] text-slate-500">{group}</div>
                      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                        {keys.sort((left, right) => parameterMetadata[left].label.localeCompare(parameterMetadata[right].label)).map((key) => renderParameterField(key))}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </section>

            <section className="col-span-12 lg:col-span-3 space-y-6">
              <div className="rounded-[28px] border border-slate-700/70 bg-slate-900/70 p-5 backdrop-blur">
                <h2 className="mb-4 flex items-center gap-2 text-sm font-black uppercase tracking-[0.24em] text-emerald-300"><Activity size={16} /> Live Pipeline</h2>
                <div className="rounded-2xl border border-slate-700 bg-slate-950/70 p-4">
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <div className="text-[10px] font-black uppercase tracking-[0.24em] text-slate-500">Tracked Mission</div>
                      <div className="text-sm font-mono text-blue-200">{activeMissionId ?? "No mission detected"}</div>
                    </div>
                    <div className={`rounded-full px-3 py-1 text-[10px] font-black uppercase tracking-[0.18em] ${wsConnected ? "bg-emerald-400 text-black" : "bg-amber-400 text-black"}`}>{wsConnected ? "Live" : "Retrying"}</div>
                  </div>
                  <div className="mt-2 text-xs text-slate-400">{activeMission ? `Mission state: ${activeMission.overall_status}` : "The dashboard tracks the latest active mission automatically."}</div>
                </div>

                <div className="mt-4 space-y-4">
                  {SERVICE_ORDER.map((service) => (
                    <div key={service} className="rounded-2xl border border-slate-700 bg-slate-950/60 p-4">
                      <div className="flex items-center justify-between text-[10px] font-black uppercase tracking-[0.22em] text-slate-500">
                        <span>{service}</span>
                        <span className="text-blue-300">{progress[service]?.progress ?? 0}%</span>
                      </div>
                      <div className="mt-3 h-2 overflow-hidden rounded-full bg-slate-900">
                        <div className={`h-full transition-all ${progress[service]?.status === "success" ? "bg-emerald-400" : progress[service]?.status === "error" ? "bg-red-400" : "bg-blue-400"}`} style={{ width: `${progress[service]?.progress ?? 0}%` }} />
                      </div>
                      <div className="mt-2 flex items-center gap-2 text-xs text-slate-300">
                        {progress[service]?.status === "success" ? <CheckCircle size={12} className="text-emerald-400" /> : progress[service]?.status === "error" ? <AlertCircle size={12} className="text-red-400" /> : <div className="h-2 w-2 animate-spin rounded-full border border-blue-300 border-t-transparent" />}
                        <span className="truncate">{progress[service]?.step || "Waiting for mission..."}</span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              <div className="rounded-[28px] border border-slate-700/70 bg-slate-900/70 p-5 backdrop-blur">
                <h2 className="mb-4 flex items-center gap-2 text-sm font-black uppercase tracking-[0.24em] text-amber-300"><Cpu size={16} /> Pod Diagnostics</h2>
                <div className="space-y-3">
                  {pods.map((pod) => (
                    <div key={pod.name} className="rounded-2xl border border-slate-700 bg-slate-950/70 p-4">
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <div className="truncate font-mono text-xs text-slate-100">{pod.name}</div>
                          <div className="mt-1 text-[10px] font-black uppercase tracking-[0.18em] text-slate-500">{formatPodPhase(pod)}</div>
                        </div>
                        {pod.oom_killed ? <div className="rounded-full bg-red-500 px-2 py-1 text-[10px] font-black uppercase tracking-[0.16em] text-white">OOM</div> : null}
                      </div>
                      <div className="mt-3 space-y-1 text-[11px] text-slate-400">
                        <div>Ready: {pod.ready ?? "n/a"}</div>
                        <div>Restarts: {pod.restarts ?? "n/a"}</div>
                        <div>Last exit: {pod.last_terminated_reason ?? "none"}{pod.last_terminated_exit_code !== null && pod.last_terminated_exit_code !== undefined ? ` (${pod.last_terminated_exit_code})` : ""}</div>
                        <div>Memory: request {pod.memory_request ?? "n/a"} / limit {pod.memory_limit ?? "n/a"}</div>
                      </div>
                    </div>
                  ))}
                  {pods.length === 0 ? <div className="rounded-2xl border border-slate-700 bg-slate-950/70 p-4 text-xs text-slate-500">No pod information available.</div> : null}
                </div>
                {podsError ? <div className="mt-3 text-xs text-amber-300">Pod API: {podsError}</div> : null}
              </div>

              <div className="rounded-[28px] border border-slate-700/70 bg-slate-950 p-0 shadow-2xl">
                <div className="flex items-center justify-between border-b border-slate-800 px-4 py-3">
                  <div className="flex items-center gap-2 text-[10px] font-black uppercase tracking-[0.24em] text-slate-500"><Terminal size={14} /> Console</div>
                  <button onClick={() => setLogs([])} className="text-slate-600 transition hover:text-white"><Trash2 size={12} /></button>
                </div>
                <div ref={logContainerRef} className="max-h-[420px] overflow-y-auto p-4 font-mono text-[10px] leading-relaxed text-emerald-300/75">
                  {logs.length === 0 ? <div className="italic text-slate-700">Engine idle. Awaiting mission start...</div> : null}
                  {logs.map((log, index) => (
                    <div key={`${log}-${index}`} className="border-l border-emerald-800/30 py-1 pl-3 break-all">{log}</div>
                  ))}
                </div>
              </div>
            </section>
          </div>
        ) : (
          <div className="flex min-h-[420px] items-center justify-center rounded-[32px] border border-slate-700/70 bg-slate-900/70 text-slate-500 backdrop-blur">
            <div className="text-center">
              <MapIcon size={36} className="mx-auto mb-3 text-slate-600" />
              <div className="text-lg font-black text-slate-300">Visualization module loading</div>
              <div className="mt-2 text-sm text-slate-500">Mission control now includes parameter editing, RAM estimation, and OOM diagnostics.</div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
