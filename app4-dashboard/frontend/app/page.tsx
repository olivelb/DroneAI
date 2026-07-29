"use client";

import React, { useEffect, useMemo, useState } from "react";
import {
  Activity,
  Boxes,
  Cpu,
  Database,
  Eye,
  PanelRightClose,
  Radio,
  ScanSearch,
  Sparkles,
  X,
} from "lucide-react";
import AuthGate from "./components/AuthGate";
import MissionLaunchBar from "./components/MissionLaunchBar";
import PhaseDetection from "./components/PhaseDetection";
import PhaseGaussian from "./components/PhaseGaussian";
import PhaseReconstruction from "./components/PhaseReconstruction";
import PhaseSetup from "./components/PhaseSetup";
import ResultsViewer from "./components/ResultsViewer";
import StatusSidebar from "./components/StatusSidebar";
import { StoreProvider, useStore } from "./lib/store";
import type { PhaseId } from "./lib/types";

const PHASES: Array<{
  id: PhaseId;
  label: string;
  shortLabel: string;
  description: string;
  icon: React.ReactNode;
}> = [
  {
    id: "setup",
    label: "Préparer",
    shortLabel: "Préparer",
    description: "Images et mission",
    icon: <Database size={17} />,
  },
  {
    id: "reconstruction",
    label: "Aligner",
    shortLabel: "Aligner",
    description: "Géométrie et GPS",
    icon: <Cpu size={17} />,
  },
  {
    id: "gaussian",
    label: "Produire",
    shortLabel: "DroneGS",
    description: "DroneGS et ortho",
    icon: <Sparkles size={17} />,
  },
  {
    id: "detection",
    label: "Détecter",
    shortLabel: "Détecter",
    description: "Tuilage et IA",
    icon: <ScanSearch size={17} />,
  },
  {
    id: "results",
    label: "Explorer",
    shortLabel: "Viewer",
    description: "Carte et vecteurs",
    icon: <Eye size={17} />,
  },
];

function PhaseContent({ phase }: { phase: PhaseId }) {
  switch (phase) {
    case "setup":
      return <PhaseSetup />;
    case "reconstruction":
      return <PhaseReconstruction />;
    case "gaussian":
      return <PhaseGaussian />;
    case "detection":
      return <PhaseDetection />;
    case "results":
      return <ResultsViewer />;
  }
}

function DashboardInner() {
  const {
    activePhase,
    setActivePhase,
    activeMission,
    selectedPath,
    wsConnected,
    authPrincipal,
    logout,
  } = useStore();
  const [monitorOpen, setMonitorOpen] = useState(false);

  useEffect(() => {
    if (!monitorOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMonitorOpen(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [monitorOpen]);

  const phaseState = (phase: PhaseId) => {
    if (phase === "setup") return selectedPath ? "ready" : "configure";
    if (!activeMission) return "waiting";
    if (phase === "reconstruction")
      return activeMission.services.COLMAP?.status ?? "waiting";
    if (phase === "gaussian") {
      const colmap = activeMission.services.COLMAP;
      if (colmap?.step === "GAUSS") return colmap.status ?? "processing";
      return colmap?.status === "success" ? "ready" : "waiting";
    }
    if (phase === "detection")
      return (
        activeMission.services.IA?.status ??
        activeMission.services.TILER?.status ??
        "waiting"
      );
    return activeMission.overall_status === "success" ? "ready" : "waiting";
  };

  const progress = useMemo(() => {
    if (!activeMission) return 0;
    const services = Object.values(activeMission.services);
    if (services.length === 0) return 0;
    return Math.round(
      services.reduce((total, service) => total + (service?.progress ?? 0), 0) /
        services.length,
    );
  }, [activeMission]);
  const isRunning = activeMission?.overall_status === "processing";

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-[700] border-b border-[#dbe3e0]/90 bg-[#f3f5f4]/92 backdrop-blur-xl">
        <div className="mx-auto flex h-16 max-w-[1920px] items-center justify-between gap-3 px-3 sm:px-5">
          <div className="flex min-w-0 items-center gap-3">
            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-[#173f3b] text-white shadow-[0_8px_20px_rgba(23,63,59,0.18)]">
              <Boxes size={18} />
            </div>
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <h1 className="truncate text-base font-bold tracking-[-0.03em] text-[#17201e] sm:text-lg">
                  DroneAI
                </h1>
                <span className="hidden rounded-full bg-[#dff5f0] px-2 py-0.5 text-[9px] font-bold uppercase tracking-[0.14em] text-[#0f766e] sm:inline">
                  Mission Studio
                </span>
              </div>
              <div className="flex items-center gap-1.5 text-[10px] text-[#77837f]">
                <Radio
                  size={9}
                  className={wsConnected ? "text-emerald-500" : "text-amber-500"}
                />
                {wsConnected ? "Temps réel" : "Reconnexion"}
              </div>
            </div>
          </div>

          <div className="flex min-w-0 items-center gap-2">
            <button
              type="button"
              onClick={() => setMonitorOpen(true)}
              className="relative flex min-h-11 min-w-11 items-center justify-center gap-2 rounded-xl border border-[#dce5e1] bg-white/80 px-3 text-[#4f5e59] transition hover:border-[#adc2bb] hover:bg-white"
              aria-label="Ouvrir le suivi de mission"
            >
              <Activity
                size={16}
                className={isRunning ? "text-[#0f766e]" : "text-[#7b8883]"}
              />
              <span className="hidden text-left lg:block">
                <span className="block text-[10px] font-bold uppercase tracking-wide text-[#87938f]">
                  Suivi
                </span>
                <span className="block max-w-28 truncate text-xs font-semibold text-[#34413d]">
                  {activeMission
                    ? isRunning
                      ? `${progress} %`
                      : activeMission.overall_status
                    : "Aucune mission"}
                </span>
              </span>
              {isRunning && (
                <span className="absolute right-1.5 top-1.5 h-2 w-2 animate-pulse rounded-full bg-emerald-500" />
              )}
            </button>
            <div className="hidden text-right text-[10px] leading-4 text-[#77837f] xl:block">
              <div className="max-w-32 truncate font-semibold text-[#44524e]">
                {authPrincipal?.subject}
              </div>
              <button
                type="button"
                onClick={() => void logout()}
                className="hover:text-[#0f766e]"
              >
                Déconnexion
              </button>
            </div>
            <MissionLaunchBar />
          </div>
        </div>
        {isRunning && (
          <div className="h-0.5 bg-[#dbe5e1]">
            <div
              className="h-full bg-[#0f766e] transition-[width] duration-500"
              style={{ width: `${progress}%` }}
            />
          </div>
        )}
      </header>

      <nav
        className="sticky top-16 z-[650] border-b border-[#e0e7e4] bg-[#f3f5f4]/95 backdrop-blur-xl"
        aria-label="Étapes du pipeline"
      >
        <div className="mx-auto flex max-w-[1500px] gap-1.5 overflow-x-auto px-3 py-2 sm:px-5">
          {PHASES.map((phase, index) => {
            const selected = activePhase === phase.id;
            const state = phaseState(phase.id);
            return (
              <button
                key={phase.id}
                type="button"
                onClick={() => setActivePhase(phase.id)}
                aria-current={selected ? "step" : undefined}
                className={`group flex min-h-12 min-w-[132px] flex-1 items-center gap-2.5 rounded-xl px-3 text-left transition ${
                  selected
                    ? "bg-[#173f3b] text-white shadow-[0_8px_20px_rgba(23,63,59,0.14)]"
                    : "text-[#5d6a66] hover:bg-white hover:text-[#23312d]"
                }`}
              >
                <span
                  className={`relative flex h-8 w-8 shrink-0 items-center justify-center rounded-xl ${
                    selected
                      ? "bg-white/12 text-[#8ce0d1]"
                      : "bg-[#e6eeeb] text-[#55716a]"
                  }`}
                >
                  {phase.icon}
                  <span
                    className={`absolute -right-0.5 -top-0.5 h-2 w-2 rounded-full ring-2 ${
                      selected ? "ring-[#173f3b]" : "ring-[#f3f5f4]"
                    } ${
                      state === "success" || state === "ready"
                        ? "bg-emerald-400"
                        : state === "processing"
                          ? "animate-pulse bg-amber-400"
                          : state === "error"
                            ? "bg-red-400"
                            : "bg-[#bdc9c5]"
                    }`}
                  />
                </span>
                <span className="min-w-0">
                  <span className="block text-xs font-bold">
                    {index + 1}. {phase.label}
                  </span>
                  <span
                    className={`hidden truncate text-[10px] sm:block ${
                      selected ? "text-white/55" : "text-[#8a9692]"
                    }`}
                  >
                    {phase.description}
                  </span>
                </span>
              </button>
            );
          })}
        </div>
      </nav>

      <main
        className={`mx-auto w-full px-3 py-5 sm:px-5 ${
          activePhase === "results" ? "max-w-[1920px]" : "max-w-[1380px]"
        }`}
      >
        <PhaseContent phase={activePhase} />
      </main>

      {monitorOpen && (
        <div className="fixed inset-0 z-[900]">
          <button
            type="button"
            aria-label="Fermer le suivi"
            onClick={() => setMonitorOpen(false)}
            className="absolute inset-0 bg-[#14201d]/35 backdrop-blur-[2px]"
          />
          <aside
            role="dialog"
            aria-modal="true"
            aria-label="Suivi de mission"
            className="absolute bottom-0 right-0 top-0 flex w-[min(430px,100%)] flex-col border-l border-[#dbe4e0] bg-[#f5f7f6] shadow-[-24px_0_70px_rgba(20,32,28,0.17)]"
          >
            <div className="flex h-16 shrink-0 items-center justify-between border-b border-[#dfe7e4] px-4">
              <div className="flex items-center gap-2">
                <PanelRightClose size={17} className="text-[#0f766e]" />
                <div>
                  <div className="text-sm font-bold text-[#273530]">
                    Suivi de mission
                  </div>
                  <div className="text-[10px] text-[#7b8883]">
                    Progression, workers et événements
                  </div>
                </div>
              </div>
              <button
                type="button"
                onClick={() => setMonitorOpen(false)}
                aria-label="Fermer"
                className="flex h-9 w-9 items-center justify-center rounded-xl border border-[#dce4e1] bg-white text-[#65726e]"
              >
                <X size={15} />
              </button>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto p-4">
              <StatusSidebar />
            </div>
          </aside>
        </div>
      )}
    </div>
  );
}

export default function Dashboard() {
  return (
    <StoreProvider>
      <AuthGate>
        <DashboardInner />
      </AuthGate>
    </StoreProvider>
  );
}
