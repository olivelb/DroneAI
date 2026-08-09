"use client";

import {
  Ban,
  CheckCircle2,
  Circle,
  LoaderCircle,
  TriangleAlert,
} from "lucide-react";
import type { MessageKey } from "../lib/i18n/catalog";
import { useI18n } from "../lib/i18n/provider";
import type { MissionStageId, MissionStageRun } from "../lib/types";

const STAGES: MissionStageId[] = [
  "reconstruction",
  "gaussian_training",
  "gaussian_filtering",
  "rasterization",
  "detection",
];

const LABELS: Record<MissionStageId, MessageKey> = {
  reconstruction: "monitor.stage.reconstruction",
  gaussian_training: "monitor.stage.gaussianTraining",
  gaussian_filtering: "monitor.stage.gaussianFiltering",
  rasterization: "monitor.stage.rasterization",
  detection: "monitor.stage.detection",
};

const statusKey = (status: string): MessageKey => {
  const keys: Record<string, MessageKey> = {
    blocked: "monitor.status.blocked",
    queued: "monitor.status.queued",
    running: "monitor.status.running",
    succeeded: "monitor.status.succeeded",
    failed: "monitor.status.failed",
    cancelled: "monitor.status.cancelled",
  };
  return keys[status] ?? "monitor.status.unknown";
};

export const latestStageRuns = (
  runs: MissionStageRun[],
): Partial<Record<MissionStageId, MissionStageRun>> => {
  const latest: Partial<Record<MissionStageId, MissionStageRun>> = {};
  for (const run of runs) {
    const current = latest[run.stage];
    if (!current || run.attempt > current.attempt) latest[run.stage] = run;
  }
  return latest;
};

const durationLabel = (run: MissionStageRun) => {
  if (!run.started_at) return null;
  const start = Date.parse(run.started_at);
  const end = run.completed_at ? Date.parse(run.completed_at) : Date.now();
  if (!Number.isFinite(start) || !Number.isFinite(end)) return null;
  const seconds = Math.max(0, Math.round((end - start) / 1000));
  if (seconds < 60) return `${seconds} s`;
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return `${minutes} min ${remainder.toString().padStart(2, "0")} s`;
};

function StageIcon({ status }: { status: string }) {
  if (status === "succeeded") return <CheckCircle2 size={18} />;
  if (status === "running") return <LoaderCircle size={18} className="animate-spin" />;
  if (status === "failed") return <TriangleAlert size={18} />;
  if (status === "cancelled") return <Ban size={18} />;
  return <Circle size={18} />;
}

const palette = (status: string) => {
  if (status === "succeeded") {
    return "border-emerald-200 bg-emerald-50 text-emerald-700";
  }
  if (status === "running") {
    return "border-teal-200 bg-teal-50 text-teal-700";
  }
  if (status === "failed") {
    return "border-red-200 bg-red-50 text-red-700";
  }
  if (status === "cancelled") {
    return "border-amber-200 bg-amber-50 text-amber-700";
  }
  return "border-[#e0e7e4] bg-[#f7f9f8] text-[#7c8985]";
};

export default function MissionStageProgress({
  runs,
  compact = false,
}: {
  runs: MissionStageRun[];
  compact?: boolean;
}) {
  const { t } = useI18n();
  const latest = latestStageRuns(runs);

  if (runs.length === 0) {
    return (
      <p className="rounded-xl border border-dashed border-[#d6dfdc] p-4 text-sm text-[#7b8883]">
        {t("monitor.noStageRuns")}
      </p>
    );
  }

  return (
    <div className={compact ? "space-y-2" : "grid gap-3 md:grid-cols-2 xl:grid-cols-5"}>
      {STAGES.map((stage, index) => {
        const run = latest[stage];
        const status = run?.status ?? "blocked";
        const duration = run ? durationLabel(run) : null;
        const progress = run
          ? status === "succeeded"
            ? 100
            : Math.max(0, Math.min(100, run.progress ?? 0))
          : 0;
        return (
          <article
            key={stage}
            className={`relative rounded-2xl border p-3.5 transition ${palette(status)}`}
          >
            <div className="flex items-start gap-3">
              <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-white/75 shadow-sm">
                <StageIcon status={status} />
              </div>
              <div className="min-w-0 flex-1">
                <div className="text-[9px] font-bold uppercase tracking-[0.14em] opacity-65">
                  {t("monitor.stageNumber", { number: index + 1 })}
                </div>
                <div className="mt-0.5 text-xs font-bold text-[#2f3c38]">
                  {t(LABELS[stage])}
                </div>
                <div className="mt-1 flex flex-wrap items-center gap-x-2 text-[10px] font-semibold">
                  <span>{t(statusKey(status))}</span>
                  {run && run.attempt > 0 && (
                    <span>{t("monitor.attempt", { number: run.attempt + 1 })}</span>
                  )}
                  {duration && <span>{duration}</span>}
                </div>
              </div>
            </div>
            <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-black/8">
              <div
                className={`h-full rounded-full transition-all duration-500 ${
                  status === "failed" ? "bg-red-500" : "bg-current"
                }`}
                style={{ width: `${progress}%` }}
              />
            </div>
            {run?.error_message && (
              <p className="mt-2 line-clamp-3 text-[10px] leading-4 text-red-700">
                {run.error_message}
              </p>
            )}
          </article>
        );
      })}
    </div>
  );
}
