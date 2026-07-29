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
  const update = (patch: Partial<AnalysisCreate>) =>
    onFormChange({ ...form, ...patch });

  return (
    <div className="space-y-4">
      <button
        type="button"
        onClick={() => onFormVisibilityChange(!formVisible)}
        className="flex min-h-11 w-full items-center justify-center gap-2 rounded-xl bg-[#173f38] text-sm font-semibold text-white hover:bg-[#0f766e]"
      >
        <Plus size={16} /> Nouvelle analyse du GeoTIFF
      </button>

      {formVisible && (
        <div className="space-y-3 rounded-2xl border border-[#cfe0da] bg-[#f8fbfa] p-3">
          <input
            value={form.name}
            onChange={(event) => update({ name: event.target.value })}
            className="input-control"
            placeholder="Nom de la couche"
          />
          <textarea
            value={form.description}
            onChange={(event) =>
              update({ description: event.target.value })
            }
            className="input-control min-h-20"
            placeholder="Description"
          />
          <div className="grid grid-cols-[1fr_52px] gap-2">
            <input
              value={form.tags.join(", ")}
              onChange={(event) =>
                update({ tags: splitTags(event.target.value) })
              }
              className="input-control"
              placeholder="Tags"
            />
            <input
              type="color"
              value={form.color}
              onChange={(event) => update({ color: event.target.value })}
              className="h-11 w-full rounded-xl border border-[#dce4e1] p-1"
              aria-label="Couleur"
            />
          </div>
          <div className="grid grid-cols-2 gap-2">
            <select
              value={form.backend}
              onChange={(event) =>
                update({ backend: event.target.value as AIBackend })
              }
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
                <option value="yolo26l">YOLO26-L · qualité</option>
                <option value="yolo26m">YOLO26-M · équilibré</option>
                <option value="yolo26s">YOLO26-S · rapide</option>
                <option value="yolo11l">YOLO11-L</option>
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
              placeholder="Classes"
            />
            <select
              value={form.tile_size}
              onChange={(event) =>
                update({ tile_size: Number(event.target.value) })
              }
              className="input-control"
            >
              <option value={512}>512 px</option>
              <option value={1024}>1024 px</option>
              <option value={2048}>2048 px</option>
            </select>
            <label className="text-xs text-[#66736f]">
              Confiance {Math.round(form.confidence * 100)} %
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
              Désactivez pour conserver les résultats uniquement en
              GeoJSON tuilé dans le stockage objet.
            </span>
          </label>
          <button
            type="button"
            onClick={onSubmit}
            disabled={submitting || !form.name.trim()}
            className="flex min-h-10 w-full items-center justify-center gap-2 rounded-xl bg-[#0f766e] text-sm font-semibold text-white disabled:opacity-50"
          >
            <Play size={14} />
            {submitting ? "Mise en file…" : "Lancer"}
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
              {run.detection_count} objets
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
                <RotateCcw size={12} /> Reprendre
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
                <XCircle size={12} /> Annuler
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
