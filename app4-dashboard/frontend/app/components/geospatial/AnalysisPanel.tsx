"use client";

import {
  Database,
  Download,
  HardDrive,
  Play,
  Plus,
  RotateCcw,
  XCircle,
} from "lucide-react";
import { getFileUrl } from "../../lib/api";
import { useI18n } from "../../lib/i18n/provider";
import { useStore } from "../../lib/store";
import type {
  AIBackend,
  AnalysisCreate,
  AnalysisRun,
} from "../../lib/types";
import { splitTags, statusTone } from "./workspace-config";

interface AnalysisPanelProps {
  analyses: AnalysisRun[];
  form: AnalysisCreate;
  formVisible: boolean;
  submitting: boolean;
  onFormChange: (form: AnalysisCreate) => void;
  onFormVisibilityChange: (visible: boolean) => void;
  onSubmit: () => void;
  onRetry: (runId: string) => void;
  onCancel: (runId: string) => void;
}

export default function AnalysisPanel({
  analyses,
  form,
  formVisible,
  submitting,
  onFormChange,
  onFormVisibilityChange,
  onSubmit,
  onRetry,
  onCancel,
}: AnalysisPanelProps) {
  const { t } = useI18n();
  const { parameterSchema } = useStore();
  const yoloModels = parameterSchema?.yolo_models ?? [];
  const sam3MaximumTileSize =
    parameterSchema?.sam3.maximum_source_tile_size ?? 1024;
  const tileSizes = [512, 1024, 2048].filter(
    (size) => form.backend !== "sam3" || size <= sam3MaximumTileSize,
  );
  const modelAvailable =
    form.backend !== "yolo" ||
    yoloModels.some(
      (model) => model.id === form.model_variant && model.available,
    );
  const update = (patch: Partial<AnalysisCreate>) =>
    onFormChange({ ...form, ...patch });

  return (
    <div className="space-y-4">
      <button
        type="button"
        onClick={() => onFormVisibilityChange(!formVisible)}
        className="flex min-h-11 w-full items-center justify-center gap-2 rounded-xl bg-[#173f38] text-sm font-semibold text-white hover:bg-[#0f766e]"
      >
        <Plus size={16} /> {t("analysis.new")}
      </button>

      {formVisible && (
        <div className="space-y-3 rounded-2xl border border-[#cfe0da] bg-[#f8fbfa] p-3">
          <input
            value={form.name}
            onChange={(event) => update({ name: event.target.value })}
            className="input-control"
            placeholder={t("analysis.name")}
          />
          <textarea
            value={form.description}
            onChange={(event) =>
              update({ description: event.target.value })
            }
            className="input-control min-h-20"
            placeholder={t("analysis.description")}
          />
          <div className="grid grid-cols-[1fr_52px] gap-2">
            <input
              value={form.tags.join(", ")}
              onChange={(event) =>
                update({ tags: splitTags(event.target.value) })
              }
              className="input-control"
              placeholder={t("analysis.tags")}
            />
            <input
              type="color"
              value={form.color}
              onChange={(event) => update({ color: event.target.value })}
              className="h-11 w-full rounded-xl border border-[#dce4e1] p-1"
              aria-label={t("analysis.color")}
            />
          </div>
          <div className="grid grid-cols-2 gap-2">
            <select
              value={form.backend}
              onChange={(event) => {
                const backend = event.target.value as AIBackend;
                update({
                  backend,
                  tile_size:
                    backend === "sam3" &&
                    form.tile_size > sam3MaximumTileSize
                      ? sam3MaximumTileSize
                      : form.tile_size,
                });
              }}
              className="input-control"
            >
              <option value="yolo">YOLO OBB</option>
              <option value="sam3">SAM 3</option>
            </select>
            {form.backend === "yolo" ? (
              <select
                value={form.model_variant}
                onChange={(event) =>
                  update({ model_variant: event.target.value })
                }
                className="input-control"
              >
                {yoloModels.map((model) => (
                  <option
                    key={model.id}
                    value={model.id}
                    disabled={!model.available}
                  >
                    {model.label}
                  </option>
                ))}
              </select>
            ) : (
              <input
                value={form.prompt}
                onChange={(event) =>
                  update({ prompt: event.target.value })
                }
                className="input-control"
                placeholder="Prompt SAM"
              />
            )}
            <input
              value={form.classes.join(",")}
              onChange={(event) =>
                update({ classes: splitTags(event.target.value) })
              }
              className="input-control"
              placeholder={t("analysis.classes")}
            />
            <select
              value={form.tile_size}
              onChange={(event) =>
                update({ tile_size: Number(event.target.value) })
              }
              className="input-control"
            >
              {tileSizes.map((size) => (
                <option key={size} value={size}>
                  {size} px
                </option>
              ))}
            </select>
            <label className="text-xs text-[#66736f]">
              {t("analysis.confidence", {
                percent: Math.round(form.confidence * 100),
              })}
              <input
                type="range"
                min="0.05"
                max="0.9"
                step="0.05"
                value={form.confidence}
                onChange={(event) =>
                  update({ confidence: Number(event.target.value) })
                }
                className="mt-1 w-full accent-[#0f766e]"
              />
            </label>
          </div>
          <label className="flex items-start gap-2 rounded-xl bg-white p-2 text-xs text-[#53615d]">
            <input
              type="checkbox"
              checked={form.persist_results}
              onChange={(event) =>
                update({ persist_results: event.target.checked })
              }
              className="mt-0.5 accent-[#0f766e]"
            />
            <span>
              <strong>Index PostGIS</strong>
              <br />
              {t("analysis.postgisHelp")}
            </span>
          </label>
          <button
            type="button"
            onClick={onSubmit}
            disabled={submitting || !form.name.trim() || !modelAvailable}
            className="flex min-h-10 w-full items-center justify-center gap-2 rounded-xl bg-[#0f766e] text-sm font-semibold text-white disabled:opacity-50"
          >
            <Play size={14} />
            {submitting ? t("analysis.queueing") : t("analysis.launch")}
          </button>
        </div>
      )}

      {analyses.map((run) => (
        <div
          key={run.run_id}
          className="rounded-2xl border border-[#dce4e1] p-3"
        >
          <div className="flex items-start gap-2">
            <span
              className="mt-1 h-3 w-3 rounded-full"
              style={{ backgroundColor: run.color }}
            />
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-semibold text-[#26332f]">
                {run.name}
              </div>
              <div className="mt-1 flex flex-wrap gap-1">
                <span
                  className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${statusTone(run.status)}`}
                >
                  {run.status}
                </span>
                <span className="rounded-full bg-slate-50 px-2 py-0.5 text-[10px] text-slate-500">
                  {run.persist_results ? (
                    <Database size={10} className="inline" />
                  ) : (
                    <HardDrive size={10} className="inline" />
                  )}{" "}
                  {run.persist_results ? "PostGIS" : "GeoJSON"}
                </span>
              </div>
            </div>
          </div>
          <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-[#edf1ef]">
            <div
              className="h-full bg-[#47aa98]"
              style={{ width: `${run.progress}%` }}
            />
          </div>
          <div className="mt-1 flex justify-between text-[10px] text-[#81908b]">
            <span>{run.phase}</span>
            <span>
              {run.tiles_completed}/{run.total_tiles} ·{" "}
              {t("analysis.objects", { count: run.detection_count })}
            </span>
          </div>
          {run.error_message && (
            <p className="mt-2 rounded-lg bg-rose-50 p-2 text-[11px] text-rose-700">
              {run.error_message}
            </p>
          )}
          <div className="mt-2 flex gap-2">
            {run.status === "failed" && (
              <button
                type="button"
                onClick={() => onRetry(run.run_id)}
                className="flex min-h-9 flex-1 items-center justify-center gap-1 rounded-lg border border-[#d4dfdb] text-xs"
              >
                <RotateCcw size={12} /> {t("analysis.retry")}
              </button>
            )}
            {["queued", "tiling", "running", "finalizing"].includes(
              run.status,
            ) && (
              <button
                type="button"
                onClick={() => onCancel(run.run_id)}
                className="flex min-h-9 flex-1 items-center justify-center gap-1 rounded-lg border border-rose-200 text-xs text-rose-700"
              >
                <XCircle size={12} /> {t("analysis.cancel")}
              </button>
            )}
            {run.result_s3_key && (
              <a
                href={getFileUrl(run.result_s3_key)}
                className="flex min-h-9 flex-1 items-center justify-center gap-1 rounded-lg border border-[#d4dfdb] text-xs"
              >
                <Download size={12} /> GeoJSON
              </a>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
