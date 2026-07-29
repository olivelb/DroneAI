"use client";

import React from "react";
import {
  Boxes,
  Cpu,
  Database,
  Eye,
  Radio,
  ScanSearch,
  Sparkles,
} from "lucide-react";
import { StoreProvider, useStore } from "./lib/store";
import MissionLaunchBar from "./components/MissionLaunchBar";
import AuthGate from "./components/AuthGate";
import PhaseDetection from "./components/PhaseDetection";
import PhaseGaussian from "./components/PhaseGaussian";
import PhaseReconstruction from "./components/PhaseReconstruction";
import PhaseSetup from "./components/PhaseSetup";
import ResultsViewer from "./components/ResultsViewer";
import StatusSidebar from "./components/StatusSidebar";
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
    label: "Mission setup",
    shortLabel: "Setup",
    description: "Dataset and mission identity",
    icon: <Database size={17} />,
  },
  {
    id: "reconstruction",
    label: "Reconstruction",
    shortLabel: "Align",
    description: "COLMAP, GLOMAP and georeferencing",
    icon: <Cpu size={17} />,
  },
  {
    id: "gaussian",
    label: "DroneGS & ortho",
    shortLabel: "DroneGS",
    description: "Training, quality gates and filters",
    icon: <Sparkles size={17} />,
  },
  {
    id: "detection",
    label: "Detection",
    shortLabel: "Detect",
    description: "Tiling, YOLO OBB or SAM",
    icon: <ScanSearch size={17} />,
  },
  {
    id: "results",
    label: "Results",
    shortLabel: "Results",
    description: "Maps, point clouds and exports",
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

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-40 border-b border-[#dbe3e0]/90 bg-[#f3f5f4]/90 backdrop-blur-xl">
        <div className="mx-auto flex h-[76px] max-w-[1920px] items-center justify-between gap-4 px-4 sm:px-6">
          <div className="flex min-w-0 items-center gap-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl bg-[#173f3b] text-white shadow-[0_8px_20px_rgba(23,63,59,0.18)]">
              <Boxes size={20} />
            </div>
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <h1 className="truncate text-lg font-bold tracking-[-0.03em] text-[#17201e] sm:text-xl">
                  DroneAI
                </h1>
                <span className="hidden rounded-full bg-[#dff5f0] px-2 py-0.5 text-[9px] font-bold uppercase tracking-[0.14em] text-[#0f766e] sm:inline">
                  Mission Studio
                </span>
              </div>
              <div className="flex items-center gap-1.5 text-[11px] text-[#77837f]">
                <Radio
                  size={10}
                  className={wsConnected ? "text-emerald-500" : "text-amber-500"}
                />
                {wsConnected ? "Live telemetry" : "Connecting telemetry"}
              </div>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <div className="hidden text-right text-[10px] leading-4 text-[#77837f] md:block">
              <div className="font-semibold text-[#44524e]">
                {authPrincipal?.subject}
              </div>
              <button
                type="button"
                onClick={() => void logout()}
                className="hover:text-[#0f766e]"
              >
                Sign out · {authPrincipal?.role}
              </button>
            </div>
            <MissionLaunchBar />
          </div>
        </div>
      </header>

      <div className="mx-auto grid w-full max-w-[1920px] gap-5 px-3 py-4 sm:px-5 lg:grid-cols-[238px_minmax(0,1fr)] lg:py-6 xl:grid-cols-[238px_minmax(0,1fr)_322px]">
        <aside className="hidden lg:block">
          <div className="surface sticky top-[100px] p-3">
            <div className="px-3 pb-3 pt-2">
              <div className="eyebrow">Workflow</div>
              <p className="mt-1 text-xs leading-5 text-[#7a8783]">
                Configure once, then follow each production stage.
              </p>
            </div>
            <nav className="space-y-1.5" aria-label="Pipeline phases">
              {PHASES.map((phase, index) => {
                const selected = activePhase === phase.id;
                const state = phaseState(phase.id);
                return (
                  <button
                    key={phase.id}
                    type="button"
                    onClick={() => setActivePhase(phase.id)}
                    className={`group flex w-full items-start gap-3 rounded-2xl px-3 py-3 text-left transition ${
                      selected
                        ? "bg-[#173f3b] text-white shadow-[0_10px_25px_rgba(23,63,59,0.15)]"
                        : "text-[#53615d] hover:bg-[#f1f6f4] hover:text-[#21302c]"
                    }`}
                  >
                    <span
                      className={`mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-xl ${
                        selected
                          ? "bg-white/12 text-[#8ce0d1]"
                          : "bg-[#edf3f1] text-[#55716a]"
                      }`}
                    >
                      {phase.icon}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="flex items-center justify-between gap-2">
                        <span className="text-sm font-semibold">
                          {phase.label}
                        </span>
                        <span
                          className={`h-2 w-2 shrink-0 rounded-full ${
                            state === "success" || state === "ready"
                              ? "bg-emerald-400"
                              : state === "processing"
                                ? "animate-pulse bg-amber-400"
                                : state === "error"
                                  ? "bg-red-400"
                                  : selected
                                    ? "bg-white/35"
                                    : "bg-[#cbd5d1]"
                          }`}
                          title={state}
                        />
                      </span>
                      <span
                        className={`mt-0.5 block text-[10px] leading-4 ${
                          selected ? "text-white/60" : "text-[#8a9692]"
                        }`}
                      >
                        {index + 1}. {phase.description}
                      </span>
                    </span>
                  </button>
                );
              })}
            </nav>
          </div>
        </aside>

        <main
          className={`min-w-0 ${
            activePhase === "results" ? "xl:col-span-2" : ""
          }`}
        >
          <nav
            className="mb-4 flex gap-2 overflow-x-auto pb-1 lg:hidden"
            aria-label="Pipeline phases"
          >
            {PHASES.map((phase) => (
              <button
                key={phase.id}
                type="button"
                onClick={() => setActivePhase(phase.id)}
                className={`flex min-h-11 shrink-0 items-center gap-2 rounded-xl border px-3 text-xs font-semibold transition ${
                  activePhase === phase.id
                    ? "border-[#173f3b] bg-[#173f3b] text-white"
                    : "border-[#dce4e1] bg-white text-[#5c6965]"
                }`}
              >
                {phase.icon}
                {phase.shortLabel}
              </button>
            ))}
          </nav>

          <PhaseContent phase={activePhase} />

          {activePhase !== "results" && (
            <details className="surface mt-5 xl:hidden">
              <summary className="cursor-pointer list-none px-5 py-4 text-sm font-semibold text-[#273530]">
                Mission status and live console
              </summary>
              <div className="border-t border-[#e5ebe8] p-4">
                <StatusSidebar />
              </div>
            </details>
          )}
        </main>

        {activePhase !== "results" && (
          <aside className="hidden xl:block">
            <div className="sticky top-[100px] max-h-[calc(100vh-116px)] overflow-y-auto">
              <StatusSidebar />
            </div>
          </aside>
        )}
      </div>
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
