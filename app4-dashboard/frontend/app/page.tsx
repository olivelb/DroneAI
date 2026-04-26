"use client";

import React from "react";
import {
  Database, Cpu, Sparkles, Search, Eye,
} from "lucide-react";
import { StoreProvider, useStore } from "./lib/store";
import StatusSidebar from "./components/StatusSidebar";
import PhaseSetup from "./components/PhaseSetup";
import PhaseReconstruction from "./components/PhaseReconstruction";
import PhaseGaussian from "./components/PhaseGaussian";
import PhaseDetection from "./components/PhaseDetection";
import ResultsViewer from "./components/ResultsViewer";
import type { PhaseId } from "./lib/types";

const PHASES: Array<{ id: PhaseId; label: string; shortLabel: string; icon: React.ReactNode }> = [
  { id: "setup",          label: "Mission Setup",        shortLabel: "Setup",          icon: <Database size={16} /> },
  { id: "reconstruction", label: "Reconstruction",       shortLabel: "Reconstruct",    icon: <Cpu size={16} /> },
  { id: "gaussian",       label: "Gaussian & Ortho",     shortLabel: "Gaussian",       icon: <Sparkles size={16} /> },
  { id: "detection",      label: "Tiling & Detection",   shortLabel: "Detection",      icon: <Search size={16} /> },
  { id: "results",        label: "Results",              shortLabel: "Results",         icon: <Eye size={16} /> },
];

function PhaseContent({ phase }: { phase: PhaseId }) {
  switch (phase) {
    case "setup": return <PhaseSetup />;
    case "reconstruction": return <PhaseReconstruction />;
    case "gaussian": return <PhaseGaussian />;
    case "detection": return <PhaseDetection />;
    case "results": return <ResultsViewer />;
  }
}

function DashboardInner() {
  const { activePhase, setActivePhase, activeMission, wsConnected } = useStore();

  return (
    <div className="flex min-h-screen flex-col bg-gray-50">
      {/* Header */}
      <header className="border-b border-gray-200 bg-white px-6 py-4">
        <div className="mx-auto flex max-w-[1800px] items-center justify-between">
          <div>
            <h1 className="text-xl font-bold tracking-tight text-gray-900">DroneAI</h1>
            <p className="text-xs text-gray-400">Photogrammetry Pipeline</p>
          </div>
          <div className="flex items-center gap-2">
            {activeMission && (
              <span className="rounded-full bg-gray-100 px-3 py-1 font-mono text-xs text-gray-600">
                {activeMission.vol_id}
              </span>
            )}
            <span className={`inline-block h-2 w-2 rounded-full ${wsConnected ? "bg-emerald-400" : "bg-red-400"}`} />
          </div>
        </div>
      </header>

      {/* Phase tabs */}
      <nav className="border-b border-gray-200 bg-white">
        <div className="mx-auto flex max-w-[1800px] gap-1 px-6">
          {PHASES.map((p, i) => (
            <button
              key={p.id}
              onClick={() => setActivePhase(p.id)}
              className={`flex items-center gap-2 border-b-2 px-4 py-3 text-sm font-medium transition ${
                activePhase === p.id
                  ? "border-blue-500 text-blue-600"
                  : "border-transparent text-gray-500 hover:text-gray-700"
              }`}
            >
              <span className={`flex h-5 w-5 items-center justify-center rounded-full text-[10px] font-bold ${
                activePhase === p.id ? "bg-blue-500 text-white" : "bg-gray-200 text-gray-500"
              }`}>
                {i}
              </span>
              {p.icon}
              <span className="hidden sm:inline">{p.label}</span>
              <span className="sm:hidden">{p.shortLabel}</span>
            </button>
          ))}
        </div>
      </nav>

      {/* Body */}
      <main className="mx-auto flex w-full max-w-[1800px] flex-1 gap-6 p-6">
        {/* Phase content */}
        <div className={`flex-1 ${activePhase === "results" ? "" : "max-w-[1100px]"}`}>
          <PhaseContent phase={activePhase} />
        </div>

        {/* Status sidebar — hidden on large results view */}
        {activePhase !== "results" && (
          <div className="hidden w-80 shrink-0 lg:block">
            <StatusSidebar />
          </div>
        )}
      </main>
    </div>
  );
}

export default function Dashboard() {
  return (
    <StoreProvider>
      <DashboardInner />
    </StoreProvider>
  );
}
