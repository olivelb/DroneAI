"use client";

import React, { useState, useEffect, useRef } from "react";
import { Play, Settings, Database, Activity, Map as MapIcon, CheckCircle, AlertCircle, Folder, File, ChevronRight, Home, Terminal, Trash2 } from "lucide-react";

type ServiceName = "COLMAP" | "TILER" | "IA";

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

type PodState = {
  name: string;
  phase: string;
  ready: string | null;
  restarts: number | null;
  reason?: string | null;
};

const SERVICE_ORDER: ServiceName[] = ["COLMAP", "TILER", "IA"];

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

const formatPodPhase = (pod: PodState) => {
  const phase = pod.phase || "unknown";
  if (pod.reason) {
    return `${phase} (${pod.reason})`;
  }
  return phase;
};

export default function Dashboard() {
  const [currentPath, setCurrentPath] = useState("/host/mnt/j");
  const [items, setItems] = useState<any[]>([]);
  const [selectedPath, setSelectedPath] = useState("");
  const [workspacePath, setWorkspacePath] = useState("/mnt/j/workspace");
  const [volId, setVolId] = useState("vol_" + Math.floor(Math.random() * 1000));
  const [missions, setMissions] = useState<Record<string, MissionSummary>>({});
  const [activeMissionId, setActiveMissionId] = useState<string | null>(null);
  const [logs, setLogs] = useState<string[]>([]);
  const [pods, setPods] = useState<PodState[]>([]);
  const [podsError, setPodsError] = useState<string | null>(null);
  const [wsConnected, setWsConnected] = useState(false);
  const [activeTab, setActiveTab] = useState("control");
  
  // AI Settings
  const [aiConfidence, setAiConfidence] = useState(0.5);
  const [selectedClasses, setSelectedClasses] = useState<string[]>(["car"]);
  const [pipeline, setPipeline] = useState<"modern" | "legacy">("modern");
  const AVAILABLE_CLASSES = ["person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat"];

  const logContainerRef = useRef<HTMLDivElement>(null);

  const progress = activeMissionId ? missions[activeMissionId]?.services ?? {} : {};
  const activeMission = activeMissionId ? missions[activeMissionId] : null;

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
    const latestMission = Object.values(missionMap)
      .sort((left, right) => right.updated_at - left.updated_at)[0];
    return latestMission ? latestMission.vol_id : null;
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

  // Browse when path changes
  useEffect(() => {
    browse(currentPath);
  }, [currentPath]);

  useEffect(() => {
    void refreshSummary();
    void refreshPods();

    const summaryInterval = setInterval(() => {
      void refreshSummary();
    }, 5000);
    const podInterval = setInterval(() => {
      void refreshPods();
    }, 10000);

    return () => {
      clearInterval(summaryInterval);
      clearInterval(podInterval);
    };
  }, []);

  // Single WebSocket lifecycle — connect once on mount, disconnect on unmount
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
  }, []);

  useEffect(() => {
    if (logContainerRef.current) {
      logContainerRef.current.scrollTop = logContainerRef.current.scrollHeight;
    }
  }, [logs]);

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
    } catch (err) {
      console.error("Browse error:", err);
    }
  };

  const startPipeline = async () => {
    setLogs(["[SYSTEM] Starting pipeline..."]);
    setActiveMissionId(volId);
    const params = {
      vol_id: volId,
      input_dir: selectedPath,
      workspace_dir: workspacePath,
      epsg: "EPSG:4326",
      camera_model: "PINHOLE",
      pipeline,
      tile_size: 1024,
      ai_confidence: aiConfidence,
      classes: selectedClasses
    };

    try {
      const res = await fetch(`${getApiBaseUrl()}/mission`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(params)
      });
      const result = await res.json();
      console.log("Mission started:", result);
      setLogs(prev => [...prev, `[SYSTEM] Mission ${volId} sent successfully.`]);
    } catch (err) {
      setLogs(prev => [...prev, `[SYSTEM] Error starting mission: ${err}`]);
    }
  };

  const cancelPipeline = async () => {
    try {
      const targetMissionId = activeMissionId ?? volId;
      const res = await fetch(`${getApiBaseUrl()}/mission/cancel?vol_id=${encodeURIComponent(targetMissionId)}`, {
        method: "POST"
      });
      const result = await res.json();
      setLogs(prev => [...prev, `[SYSTEM] Cancel command sent: ${result.message}`]);
    } catch (err) {
      setLogs(prev => [...prev, `[SYSTEM] Error canceling mission: ${err}`]);
    }
  };

  const goUp = () => {
    const parts = currentPath.split("/").filter(Boolean);
    parts.pop();
    browse("/" + parts.join("/"));
  };

  return (
    <div className="h-screen bg-slate-900 text-slate-100 p-6 flex flex-col gap-6 overflow-hidden">
      <header className="flex justify-between items-center border-b border-slate-700 pb-4">
        <div>
          <h1 className="text-3xl font-bold bg-gradient-to-r from-blue-400 to-emerald-400 bg-clip-text text-transparent">
            DroneAI Control Center
          </h1>
          <p className="text-slate-400 text-xs mt-1 font-medium tracking-wide">End-to-end photogrammetry & detection pipeline</p>
        </div>
        <div className="flex gap-3">
          <button 
            onClick={() => setActiveTab("control")}
            className={`px-4 py-2 rounded-xl flex items-center gap-2 text-sm font-bold transition-all ${activeTab === "control" ? "bg-blue-600 shadow-lg shadow-blue-900/40" : "bg-slate-800 hover:bg-slate-700 text-slate-400"}`}
          >
            <Settings size={16} /> Mission Control
          </button>
          <button 
            onClick={() => setActiveTab("map")}
            className={`px-4 py-2 rounded-xl flex items-center gap-2 text-sm font-bold transition-all ${activeTab === "map" ? "bg-blue-600 shadow-lg shadow-blue-900/40" : "bg-slate-800 hover:bg-slate-700 text-slate-400"}`}
          >
            <MapIcon size={16} /> Data Results
          </button>
        </div>
      </header>

      {activeTab === "control" ? (
        <div className="flex-1 grid grid-cols-12 gap-6 min-h-0">
          {/* LEFT: File Explorer (Column 1-4) */}
          <div className="col-span-12 lg:col-span-4 bg-slate-800 rounded-3xl border border-slate-700 shadow-2xl flex flex-col overflow-hidden">
            <div className="p-5 border-b border-slate-700">
              <h2 className="text-lg font-bold flex items-center gap-2 text-blue-400">
                <Folder size={20} /> Data Explorer
              </h2>
              <div className="mt-3 bg-slate-900 p-2 rounded-xl flex items-center gap-2 text-xs font-mono border border-slate-700/50">
                 <button onClick={() => browse("/")} className="hover:text-blue-400 p-1"><Home size={14}/></button>
                 <ChevronRight size={12} className="text-slate-600"/>
                 <span className="truncate flex-1 text-slate-400">{currentPath}</span>
                 <button onClick={goUp} className="px-2 py-1 bg-slate-800 hover:bg-white hover:text-black rounded-lg text-xs font-bold transition">Up</button>
              </div>
            </div>

            <div className="flex-1 overflow-y-auto p-3 space-y-1 scrollbar-thin scrollbar-thumb-slate-700">
              {items.map((item) => (
                <div 
                  key={item.path}
                  onClick={() => setSelectedPath(item.path)}
                  onDoubleClick={() => item.is_dir ? browse(item.path) : null}
                  className={`group flex items-center gap-3 p-3 rounded-2xl cursor-pointer transition-all border ${
                    selectedPath === item.path 
                      ? "bg-blue-600 border-blue-400 shadow-lg shadow-blue-900/20" 
                      : "bg-slate-900/30 border-transparent hover:bg-slate-700/50 hover:border-slate-600"
                  }`}
                >
                  <div 
                    onClick={(e) => {
                      if (item.is_dir) {
                        e.stopPropagation();
                        browse(item.path);
                      }
                    }}
                    className={`p-2 rounded-xl transition ${selectedPath === item.path ? "bg-blue-500/50" : "hover:bg-blue-500/20"}`}
                  >
                    {item.is_dir ? <Folder size={18} className="text-amber-400" /> : <File size={18} className="text-slate-500" />}
                  </div>
                  
                  <div className="flex-1 min-w-0">
                    <div className={`text-sm font-bold truncate ${selectedPath === item.path ? "text-white" : "text-slate-300"}`}>
                      {item.name}
                    </div>
                    {item.is_dir && item.image_count > 0 && (
                      <div className={`text-[10px] font-black uppercase tracking-tighter ${selectedPath === item.path ? "text-blue-200" : "text-emerald-500"}`}>
                        {item.image_count} images found
                      </div>
                    )}
                  </div>
                </div>
              ))}
              {items.length === 0 && (
                <div className="h-full flex flex-col items-center justify-center text-slate-600 italic text-sm">
                  <Folder size={48} className="mb-4 opacity-10" />
                  Empty folder or restricted access
                </div>
              )}
            </div>

            <div className="p-4 bg-slate-900 border-t border-slate-700">
              <div className="text-[10px] font-black text-slate-500 uppercase mb-2">Selected Dataset</div>
              <div className="bg-slate-800 p-2 rounded-xl border border-blue-500/30 text-xs text-blue-400 font-mono truncate">
                {selectedPath || "None selected"}
              </div>
            </div>
          </div>

          {/* RIGHT PANES (Column 5-12) */}
          <div className="col-span-12 lg:col-span-8 flex flex-col gap-6 overflow-hidden">
            
            {/* TOP RIGHT: Mission Config */}
            <div className="bg-slate-800 p-6 rounded-3xl border border-slate-700 shadow-xl space-y-6">
              <div className="flex justify-between items-start">
                <h2 className="text-lg font-bold flex items-center gap-2 text-blue-400">
                  <Settings size={20} /> Mission Configuration
                </h2>
                <div className="flex gap-2">
                  <button 
                    onClick={startPipeline}
                    disabled={!selectedPath || !workspacePath}
                    className="bg-emerald-600 hover:bg-emerald-500 disabled:bg-slate-700 disabled:cursor-not-allowed text-white text-sm font-black px-6 py-2 rounded-xl flex items-center gap-2 transition shadow-lg shadow-emerald-900/20"
                  >
                    <Play size={16} fill="currentColor" /> RUN MISSION
                  </button>
                  <button 
                    onClick={cancelPipeline}
                    className="bg-red-600 hover:bg-red-500 text-white text-sm font-black px-6 py-2 rounded-xl flex items-center gap-2 transition shadow-lg shadow-red-900/20"
                  >
                    CANCEL
                  </button>
                </div>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                {/* Basic Info */}
                <div className="space-y-4">
                  <div>
                    <label className="block text-[10px] font-black text-slate-500 uppercase mb-2">Project Vol ID</label>
                    <input type="text" value={volId} onChange={(e) => setVolId(e.target.value)} className="w-full bg-slate-900 border border-slate-700 rounded-xl p-3 text-sm font-mono outline-none focus:border-blue-500" />
                  </div>
                  
                  <div className="p-4 bg-amber-900/10 border border-amber-500/20 rounded-2xl">
                    <label className="text-[10px] font-black text-amber-500 uppercase mb-2 flex items-center gap-2">
                       <Database size={12} /> Local Workspace (SSD Preferred)
                    </label>
                    <input 
                      type="text" 
                      value={workspacePath} 
                      onChange={(e) => setWorkspacePath(e.target.value)}
                      className="w-full bg-slate-900 border border-slate-700 rounded-xl p-2 text-xs font-mono outline-none focus:border-amber-500" 
                    />
                  </div>
                </div>

                {/* Engine Settings */}
                <div className="space-y-4">
                  <div className="p-4 bg-purple-900/10 border border-purple-500/20 rounded-2xl">
                    <label className="text-[10px] font-black text-purple-400 uppercase mb-3 flex items-center gap-2">
                      COLMAP ENGINE
                    </label>
                    <div className="grid grid-cols-2 gap-2 bg-slate-900 p-1 rounded-xl">
                      <button
                        onClick={() => setPipeline("modern")}
                        className={`px-3 py-2 text-[10px] font-black rounded-lg transition-all ${
                          pipeline === "modern" ? "bg-purple-600 text-white shadow-lg" : "text-slate-500 hover:text-slate-300"
                        }`}
                      >
                        MODERN (C4)
                      </button>
                      <button
                        onClick={() => setPipeline("legacy")}
                        className={`px-3 py-2 text-[10px] font-black rounded-lg transition-all ${
                          pipeline === "legacy" ? "bg-amber-600 text-white shadow-lg" : "text-slate-500 hover:text-slate-300"
                        }`}
                      >
                        LEGACY (C3)
                      </button>
                    </div>
                  </div>

                  <div className="p-4 bg-blue-900/10 border border-blue-500/20 rounded-2xl">
                    <label className="text-[10px] font-black text-blue-400 uppercase mb-3 flex justify-between">
                      <span>AI CONFIDENCE</span>
                      <span>{Math.round(aiConfidence * 100)}%</span>
                    </label>
                    <input 
                      type="range" 
                      min="0.1" max="0.9" step="0.05" 
                      value={aiConfidence} 
                      onChange={(e) => setAiConfidence(parseFloat(e.target.value))}
                      className="w-full accent-blue-500 h-1 bg-slate-700 rounded-lg appearance-none cursor-pointer" 
                    />
                  </div>
                </div>
              </div>

              {/* Target Classes */}
              <div className="border-t border-slate-700 pt-4">
                <label className="block text-[10px] font-black text-slate-500 uppercase mb-3">AI Detection Target Classes (YOLOv11)</label>
                <div className="flex flex-wrap gap-2">
                  {AVAILABLE_CLASSES.map(cls => (
                    <button
                      key={cls}
                      onClick={() => {
                        setSelectedClasses(prev => 
                          prev.includes(cls) ? prev.filter(c => c !== cls) : [...prev, cls]
                        )
                      }}
                      className={`px-3 py-1.5 rounded-xl text-[10px] font-bold transition-all border ${
                        selectedClasses.includes(cls) 
                          ? "bg-blue-600 text-white border-blue-400" 
                          : "bg-slate-900 text-slate-500 border-slate-700 hover:border-slate-500"
                      }`}
                    >
                      {cls.toUpperCase()}
                    </button>
                  ))}
                </div>
              </div>
            </div>

            {/* BOTTOM RIGHT: Status & Logs */}
            <div className="flex-1 grid grid-cols-1 md:grid-cols-2 gap-6 min-h-0">
              {/* Service Status */}
              <div className="bg-slate-800 p-5 rounded-3xl border border-slate-700 shadow-xl overflow-y-auto">
                <h2 className="text-sm font-black flex items-center gap-2 text-emerald-400 uppercase tracking-widest mb-4">
                  <Activity size={16} /> Live Pipeline
                </h2>
                <div className="mb-4 rounded-2xl border border-slate-700/60 bg-slate-900/40 p-3">
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <div className="text-[10px] font-black uppercase tracking-widest text-slate-500">Tracked Mission</div>
                      <div className="text-sm font-mono text-blue-300">{activeMissionId ?? "No mission detected"}</div>
                    </div>
                    <div className="text-right">
                      <div className="text-[10px] font-black uppercase tracking-widest text-slate-500">Status Stream</div>
                      <div className={`text-xs font-bold ${wsConnected ? "text-emerald-400" : "text-amber-400"}`}>{wsConnected ? "connected" : "reconnecting"}</div>
                    </div>
                  </div>
                  <div className="mt-2 text-[10px] text-slate-400">
                    {activeMission ? `Mission state: ${activeMission.overall_status}` : "The frontend will follow the most recently active mission automatically."}
                  </div>
                </div>
                <div className="space-y-5">
                  {SERVICE_ORDER.map((service) => (
                    <div key={service} className="bg-slate-900/50 p-3 rounded-2xl border border-slate-700/50">
                      <div className="flex justify-between items-center mb-2">
                        <span className="text-[10px] font-black text-slate-500">{service} ENGINE</span>
                        <span className="text-[10px] font-mono text-blue-400">{progress[service]?.progress ?? 0}%</span>
                      </div>
                      <div className="w-full bg-slate-900 h-1.5 rounded-full overflow-hidden border border-slate-800">
                        <div className={`h-full transition-all duration-1000 ${progress[service]?.status === 'success' ? 'bg-emerald-500' : progress[service]?.status === 'error' ? 'bg-red-500' : progress[service] ? 'bg-blue-500 shadow-[0_0_10px_rgba(59,130,246,0.5)]' : 'bg-slate-700'}`} style={{ width: `${progress[service]?.progress ?? 0}%` }}></div>
                      </div>
                      <div className="mt-2 flex items-center gap-2 text-[9px]">
                        {progress[service]?.status === 'success' ? <CheckCircle size={10} className="text-emerald-500" /> : progress[service]?.status === 'error' ? <AlertCircle size={10} className="text-red-500" /> : progress[service] ? <div className="animate-spin h-2 w-2 border border-blue-500 border-t-transparent rounded-full"></div> : null}
                        <span className="text-slate-400 font-medium truncate">{progress[service]?.step || 'Waiting for mission...'}</span>
                      </div>
                    </div>
                  ))}
                  <div className="bg-slate-900/50 p-3 rounded-2xl border border-slate-700/50">
                    <div className="flex items-center justify-between mb-3">
                      <span className="text-[10px] font-black text-slate-500">KUBERNETES PODS</span>
                      <span className="text-[9px] text-slate-500">{pods.length} visible</span>
                    </div>
                    <div className="space-y-2">
                      {pods.map((pod) => (
                        <div key={pod.name} className="rounded-xl border border-slate-800 bg-slate-950/70 px-3 py-2">
                          <div className="flex items-center justify-between gap-2">
                            <span className="truncate text-[10px] font-mono text-slate-200">{pod.name}</span>
                            <span className={`text-[9px] font-bold uppercase ${pod.phase === "running" ? "text-emerald-400" : pod.phase === "pending" ? "text-amber-400" : pod.phase === "failed" ? "text-red-400" : "text-slate-400"}`}>{formatPodPhase(pod)}</span>
                          </div>
                          <div className="mt-1 flex items-center gap-3 text-[9px] text-slate-500">
                            <span>Ready: {pod.ready ?? "n/a"}</span>
                            <span>Restarts: {pod.restarts ?? "n/a"}</span>
                          </div>
                        </div>
                      ))}
                      {pods.length === 0 && <div className="text-[10px] italic text-slate-600">No pod information available.</div>}
                    </div>
                    {podsError && <div className="mt-3 text-[9px] text-amber-400">Pod state API: {podsError}</div>}
                  </div>
                </div>
              </div>

              {/* Console Logs */}
              <div className="bg-slate-950 rounded-3xl border border-slate-700 shadow-2xl flex flex-col overflow-hidden">
                <div className="bg-slate-900/80 px-4 py-2 border-b border-slate-800 flex justify-between items-center">
                  <div className="flex items-center gap-2 text-[10px] font-black text-slate-500 uppercase tracking-widest">
                    <Terminal size={14} /> Console
                  </div>
                  <button onClick={() => setLogs([])} className="text-slate-600 hover:text-white transition"><Trash2 size={12} /></button>
                </div>
                <div 
                  ref={logContainerRef}
                  className="flex-1 overflow-y-auto p-4 font-mono text-[10px] space-y-1 text-emerald-500/60 scrollbar-thin scrollbar-thumb-slate-800"
                >
                  {logs.length === 0 && <div className="text-slate-800 italic">Engine idle. Awaiting mission start...</div>}
                  {logs.map((log, i) => (
                    <div key={i} className="border-l border-emerald-900/20 pl-2 py-0.5 leading-relaxed break-all opacity-80 hover:opacity-100 transition-opacity">{log}</div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </div>
      ) : (
        <div className="flex-1 bg-slate-800 p-8 rounded-3xl border border-slate-700 shadow-xl flex items-center justify-center italic text-slate-500">
           Visualization module loading...
        </div>
      )}
    </div>
  );
}
