"use client";

import React, { useRef, useEffect, useState } from "react";
import { Activity, Cpu, Terminal, Trash2 } from "lucide-react";
import { useStore } from "../lib/store";
import { deleteMission } from "../lib/api";
import type { ServiceName, PodState, StatusPayload } from "../lib/types";
import { SERVICE_ORDER } from "../lib/types";

function ServiceBar({ name, data }: { name: ServiceName; data?: StatusPayload }) {
  const pct = data?.progress ?? 0;
  const step = data?.step ?? "—";
  const status = data?.status ?? "idle";
  const color = status === "success" ? "bg-emerald-500" : status === "error" ? "bg-red-500" : "bg-blue-500";
  return (
    <div className="rounded-xl border border-gray-100 bg-white p-3">
      <div className="flex items-center justify-between text-xs">
        <span className="font-semibold text-gray-700">{name}</span>
        <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold uppercase ${
          status === "success" ? "bg-emerald-50 text-emerald-600"
          : status === "error" ? "bg-red-50 text-red-600"
          : status === "idle" ? "bg-gray-50 text-gray-400"
          : "bg-blue-50 text-blue-600"
        }`}>{status}</span>
      </div>
      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-gray-100">
        <div className={`h-full rounded-full transition-all duration-500 ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <div className="mt-1 text-[11px] text-gray-400">{step} — {pct}%</div>
    </div>
  );
}

function PodRow({ pod }: { pod: PodState }) {
  const phase = pod.phase || "unknown";
  const label = pod.oom_killed ? `${phase} (OOM)` : pod.reason ? `${phase} (${pod.reason})` : phase;
  const ok = phase === "Running" && !pod.oom_killed;
  return (
    <div className="flex items-center justify-between rounded-lg border border-gray-100 bg-white px-3 py-2 text-xs">
      <span className="truncate font-medium text-gray-700">{pod.name}</span>
      <span className={`rounded-full px-2 py-0.5 font-semibold ${ok ? "bg-emerald-50 text-emerald-600" : "bg-amber-50 text-amber-600"}`}>
        {label}
      </span>
    </div>
  );
}

export default function StatusSidebar() {
  const { activeMission, missions, activeMissionId, setActiveMissionId, logs, setLogs, wsConnected, pods, podsError, refreshSummary } = useStore();
  const logRef = useRef<HTMLDivElement>(null);
  const services = activeMission?.services ?? {};
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [logs]);

  const handleDelete = async () => {
    if (!confirmDelete) return;
    setDeleting(true);
    try {
      await deleteMission(confirmDelete);
      if (activeMissionId === confirmDelete) setActiveMissionId(null);
      refreshSummary();
    } catch (e) {
      console.error("Delete failed:", e);
    } finally {
      setDeleting(false);
      setConfirmDelete(null);
    }
  };

  return (
    <aside className="flex h-full flex-col gap-4 overflow-y-auto">
      {/* Mission selector */}
      <div className="rounded-2xl border border-gray-100 bg-white p-4 shadow-sm">
        <h3 className="mb-3 flex items-center gap-2 text-xs font-bold uppercase tracking-wide text-gray-400">
          <Activity size={14} /> Active Mission
        </h3>
        {Object.keys(missions).length > 0 ? (
          <>
            <div className="flex gap-2">
              <select
                value={activeMissionId ?? ""}
                onChange={(e) => setActiveMissionId(e.target.value || null)}
                className="flex-1 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-sm font-mono text-gray-700 outline-none"
              >
                <option value="">Select mission</option>
                {Object.values(missions).sort((a, b) => b.updated_at - a.updated_at).map((m) => (
                  <option key={m.vol_id} value={m.vol_id}>
                    {m.vol_id} ({m.overall_status})
                  </option>
                ))}
              </select>
              {activeMissionId && (
                <button
                  onClick={() => setConfirmDelete(activeMissionId)}
                  title="Delete mission"
                  className="rounded-lg border border-gray-200 bg-gray-50 px-2 text-gray-400 hover:border-red-300 hover:bg-red-50 hover:text-red-500"
                >
                  <Trash2 size={14} />
                </button>
              )}
            </div>
            {confirmDelete && (
              <div className="mt-2 rounded-lg border border-red-200 bg-red-50 p-3">
                <p className="text-xs text-red-700">Delete <strong>{confirmDelete}</strong> and all its files?</p>
                <div className="mt-2 flex gap-2">
                  <button
                    onClick={handleDelete}
                    disabled={deleting}
                    className="rounded-md bg-red-500 px-3 py-1 text-xs font-semibold text-white hover:bg-red-600 disabled:opacity-50"
                  >
                    {deleting ? "Deleting…" : "Confirm"}
                  </button>
                  <button
                    onClick={() => setConfirmDelete(null)}
                    className="rounded-md border border-gray-200 px-3 py-1 text-xs text-gray-600 hover:bg-gray-100"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}
          </>
        ) : (
          <p className="text-xs text-gray-400">No missions yet</p>
        )}
        <div className="mt-2 flex items-center gap-2 text-[11px]">
          <span className={`inline-block h-2 w-2 rounded-full ${wsConnected ? "bg-emerald-400" : "bg-red-400"}`} />
          <span className="text-gray-400">{wsConnected ? "Live" : "Reconnecting…"}</span>
        </div>
      </div>

      {/* Service progress */}
      <div className="rounded-2xl border border-gray-100 bg-white p-4 shadow-sm">
        <h3 className="mb-3 text-xs font-bold uppercase tracking-wide text-gray-400">Pipeline Progress</h3>
        <div className="space-y-2">
          {SERVICE_ORDER.map((s) => <ServiceBar key={s} name={s} data={services[s]} />)}
        </div>
      </div>

      {/* Pod diagnostics */}
      <div className="rounded-2xl border border-gray-100 bg-white p-4 shadow-sm">
        <h3 className="mb-3 flex items-center gap-2 text-xs font-bold uppercase tracking-wide text-gray-400">
          <Cpu size={14} /> Pods
        </h3>
        <div className="space-y-1.5">
          {pods.map((p) => <PodRow key={p.name} pod={p} />)}
          {pods.length === 0 && <p className="text-xs text-gray-400">No pods found</p>}
        </div>
        {podsError && <p className="mt-2 text-xs text-amber-500">{podsError}</p>}
      </div>

      {/* Console */}
      <div className="flex-1 rounded-2xl border border-gray-100 bg-white shadow-sm">
        <div className="flex items-center justify-between border-b border-gray-50 px-4 py-2.5">
          <span className="flex items-center gap-2 text-xs font-bold uppercase tracking-wide text-gray-400">
            <Terminal size={13} /> Console
          </span>
          <button onClick={() => setLogs([])} className="text-gray-300 hover:text-gray-500"><Trash2 size={12} /></button>
        </div>
        <div ref={logRef} className="max-h-[300px] overflow-y-auto p-3 font-mono text-[11px] leading-relaxed text-gray-500">
          {logs.length === 0 && <span className="text-gray-300">Waiting for events…</span>}
          {logs.map((l, i) => <div key={i} className="break-all">{l}</div>)}
        </div>
      </div>
    </aside>
  );
}
