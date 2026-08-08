"use client";

import React, { useEffect, useRef, useState } from "react";
import {
  Activity,
  CircleDot,
  Cpu,
  Radio,
  Terminal,
  Trash2,
} from "lucide-react";
import { deleteMission } from "../lib/api";
import { useMissionRuntime } from "../lib/mission-runtime";
import { useWorkspaceData } from "../lib/workspace-data";
import { serviceOrderFor } from "../lib/types";
import type { PodState, ServiceName, StatusPayload } from "../lib/types";

const SERVICE_LABELS: Record<ServiceName, string> = {
  COLMAP: "Geometry + DroneGS",
  TILER: "Raster tiling",
  IA: "AI inference",
};

function ServiceProgress({
  name,
  data,
}: {
  name: ServiceName;
  data?: StatusPayload;
}) {
  const progress = Math.max(0, Math.min(100, data?.progress ?? 0));
  const status = data?.status ?? "idle";
  const color =
    status === "success"
      ? "bg-emerald-500"
      : status === "error"
        ? "bg-red-500"
        : status === "idle"
          ? "bg-[#cbd5d1]"
          : "bg-[#0f766e]";

  return (
    <div className="rounded-2xl border border-[#e1e8e5] bg-[#fafcfb] p-3.5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-xs font-bold text-[#34413d]">
            {SERVICE_LABELS[name]}
          </div>
          <div className="mt-0.5 text-[10px] text-[#8a9692]">
            {data?.step ?? "Waiting"}
          </div>
        </div>
        <span
          className={`rounded-full px-2 py-0.5 text-[9px] font-bold uppercase tracking-wide ${
            status === "success"
              ? "bg-emerald-100 text-emerald-700"
              : status === "error"
                ? "bg-red-100 text-red-700"
                : status === "idle"
                  ? "bg-[#edf1ef] text-[#87928e]"
                  : "bg-[#dff5f0] text-[#0f766e]"
          }`}
        >
          {status}
        </span>
      </div>
      <div className="mt-3 flex items-center gap-3">
        <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-[#e6ece9]">
          <div
            className={`h-full rounded-full transition-all duration-500 ${color}`}
            style={{ width: `${progress}%` }}
          />
        </div>
        <span className="w-8 text-right font-mono text-[10px] font-bold text-[#687571]">
          {progress}%
        </span>
      </div>
    </div>
  );
}

function PodRow({ pod }: { pod: PodState }) {
  const healthy = pod.phase === "Running" && !pod.oom_killed;
  return (
    <div className="flex items-center justify-between gap-3 border-b border-[#edf1ef] py-2.5 last:border-0">
      <span className="min-w-0 truncate text-[11px] font-semibold text-[#4d5a56]">
        {pod.name}
      </span>
      <span
        className={`shrink-0 rounded-full px-2 py-0.5 text-[9px] font-bold ${
          healthy
            ? "bg-emerald-100 text-emerald-700"
            : "bg-amber-100 text-amber-700"
        }`}
      >
        {pod.oom_killed ? "OOM" : pod.phase || pod.reason || "unknown"}
      </span>
    </div>
  );
}

export default function StatusSidebar() {
  const {
    activeMission,
    missions,
    activeMissionId,
    setActiveMissionId,
    logs,
    setLogs,
    wsConnected,
    refreshSummary,
  } = useMissionRuntime();
  const { pods, podsError } = useWorkspaceData();
  const logRef = useRef<HTMLDivElement>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [logs]);

  const removeMission = async () => {
    if (!confirmDelete) return;
    setDeleting(true);
    try {
      await deleteMission(confirmDelete);
      if (activeMissionId === confirmDelete) setActiveMissionId(null);
      refreshSummary();
    } catch (error) {
      console.error("Delete failed:", error);
    } finally {
      setDeleting(false);
      setConfirmDelete(null);
    }
  };

  const sortedMissions = Object.values(missions).sort(
    (left, right) => right.updated_at - left.updated_at,
  );
  const serviceOrder = serviceOrderFor(activeMission?.services ?? {});

  return (
    <div className="space-y-4">
      <section className="surface p-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <div className="eyebrow">Mission monitor</div>
            <div className="mt-1 flex items-center gap-1.5 text-[11px] text-[#7a8783]">
              <Radio
                size={11}
                className={wsConnected ? "text-emerald-500" : "text-amber-500"}
              />
              {wsConnected ? "Live updates" : "Reconnecting"}
            </div>
          </div>
          {activeMissionId && (
            <button
              type="button"
              aria-label="Delete active mission"
              onClick={() => setConfirmDelete(activeMissionId)}
              className="flex h-9 w-9 items-center justify-center rounded-xl border border-[#e1e8e5] text-[#9aa5a1] transition hover:border-red-200 hover:bg-red-50 hover:text-red-600"
            >
              <Trash2 size={14} />
            </button>
          )}
        </div>

        <select
          aria-label="Active mission"
          value={activeMissionId ?? ""}
          onChange={(event) => setActiveMissionId(event.target.value || null)}
          className="input-control mt-4 min-h-11 font-mono"
        >
          <option value="">No active mission</option>
          {sortedMissions.map((mission) => (
            <option key={mission.vol_id} value={mission.vol_id}>
              {mission.vol_id} · {mission.overall_status}
            </option>
          ))}
        </select>

        {confirmDelete && (
          <div className="mt-3 rounded-xl border border-red-200 bg-red-50 p-3">
            <p className="text-xs leading-5 text-red-700">
              Delete <strong>{confirmDelete}</strong> and its generated files?
            </p>
            <div className="mt-2 flex gap-2">
              <button
                type="button"
                onClick={removeMission}
                disabled={deleting}
                className="min-h-9 rounded-lg bg-red-600 px-3 text-xs font-semibold text-white disabled:opacity-50"
              >
                {deleting ? "Deleting…" : "Delete"}
              </button>
              <button
                type="button"
                onClick={() => setConfirmDelete(null)}
                className="min-h-9 rounded-lg border border-red-200 bg-white px-3 text-xs font-semibold text-red-700"
              >
                Keep
              </button>
            </div>
          </div>
        )}
      </section>

      <section className="surface p-4">
        <div className="mb-3 flex items-center gap-2">
          <Activity size={15} className="text-[#0f766e]" />
          <h3 className="text-xs font-bold uppercase tracking-[0.12em] text-[#65726e]">
            End-to-end progress
          </h3>
        </div>
        <div className="space-y-2">
          {serviceOrder.map((service) => (
            <ServiceProgress
              key={service}
              name={service}
              data={activeMission?.services?.[service]}
            />
          ))}
        </div>
      </section>

      <details className="surface">
        <summary className="flex cursor-pointer list-none items-center justify-between px-4 py-4">
          <span className="flex items-center gap-2 text-xs font-bold uppercase tracking-[0.12em] text-[#65726e]">
            <Cpu size={14} className="text-[#0f766e]" />
            Workers
          </span>
          <span className="rounded-full bg-[#edf3f1] px-2 py-0.5 text-[10px] font-bold text-[#66736f]">
            {pods.length}
          </span>
        </summary>
        <div className="border-t border-[#e7ecea] px-4 py-2">
          {pods.map((pod) => (
            <PodRow key={pod.name} pod={pod} />
          ))}
          {pods.length === 0 && (
            <p className="py-3 text-xs text-[#8a9692]">No workers reported.</p>
          )}
          {podsError && (
            <p className="pb-2 text-[11px] text-amber-700">{podsError}</p>
          )}
        </div>
      </details>

      <section className="overflow-hidden rounded-[1.25rem] border border-[#263632] bg-[#18221f] shadow-[0_18px_50px_rgba(20,32,28,0.14)]">
        <div className="flex items-center justify-between border-b border-white/8 px-4 py-3">
          <span className="flex items-center gap-2 text-[10px] font-bold uppercase tracking-[0.13em] text-[#a8bab4]">
            <Terminal size={13} />
            Live console
          </span>
          <button
            type="button"
            aria-label="Clear console"
            onClick={() => setLogs([])}
            className="text-[#6f817b] hover:text-white"
          >
            <Trash2 size={12} />
          </button>
        </div>
        <div
          ref={logRef}
          className="max-h-[280px] min-h-[150px] overflow-y-auto p-4 font-mono text-[10px] leading-5 text-[#b8c8c3]"
        >
          {logs.length === 0 && (
            <span className="flex items-center gap-2 text-[#60736c]">
              <CircleDot size={10} />
              Waiting for pipeline events…
            </span>
          )}
          {logs.map((line, index) => (
            <div key={`${index}-${line.slice(0, 12)}`} className="break-words">
              <span className="mr-2 text-[#3f9f8f]">›</span>
              {line}
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
