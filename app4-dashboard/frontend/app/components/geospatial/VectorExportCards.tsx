"use client";

import { Database, Download, MapPinned, Tags } from "lucide-react";
import { useI18n } from "../../lib/i18n/provider";

export type VectorFormat = "gpkg" | "geojson";
export type VectorScope = "all" | "ai" | "legacy";

export default function VectorExportCards({
  vectorFormat,
  vectorScope,
  annotationFormat,
  visibleOnly,
  visibleRunIds,
  busy,
  onVectorFormatChange,
  onVectorScopeChange,
  onAnnotationFormatChange,
  onVisibleOnlyChange,
  onExportVectors,
  onExportAnnotations,
}: {
  vectorFormat: VectorFormat;
  vectorScope: VectorScope;
  annotationFormat: VectorFormat;
  visibleOnly: boolean;
  visibleRunIds: string[];
  busy: string;
  onVectorFormatChange: (value: VectorFormat) => void;
  onVectorScopeChange: (value: VectorScope) => void;
  onAnnotationFormatChange: (value: VectorFormat) => void;
  onVisibleOnlyChange: (value: boolean) => void;
  onExportVectors: () => void;
  onExportAnnotations: () => void;
}) {
  const { t } = useI18n();
  return (
    <>
      <section className="rounded-2xl border border-[#dce4e1] p-3.5">
        <div className="flex items-start gap-2.5">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-[#eef2fb] text-[#4568b1]">
            <MapPinned size={15} />
          </span>
          <div>
            <h3 className="text-sm font-bold text-[#2d3a36]">
              {t("export.vectorLayer")}
            </h3>
            <p className="mt-0.5 text-[11px] leading-4 text-[#7a8783]">
              {t("export.vectorHelp")}
            </p>
          </div>
        </div>
        <div className="mt-3 grid grid-cols-2 gap-2">
          <select
            value={vectorScope}
            onChange={(event) =>
              onVectorScopeChange(event.target.value as VectorScope)
            }
            className="input-control text-xs"
          >
            <option value="all">{t("export.allSources")}</option>
            <option value="ai">{t("export.aiAnalyses")}</option>
            <option value="legacy">{t("export.initialPipeline")}</option>
          </select>
          <select
            value={vectorFormat}
            onChange={(event) =>
              onVectorFormatChange(event.target.value as VectorFormat)
            }
            className="input-control text-xs"
          >
            <option value="gpkg">{t("export.gpkgRecommended")}</option>
            <option value="geojson">GeoJSON</option>
          </select>
        </div>
        {visibleRunIds.length > 0 && vectorScope !== "legacy" && (
          <label className="mt-2.5 flex items-start gap-2 rounded-xl bg-[#f7f9f8] p-2.5 text-[11px] leading-4 text-[#61706b]">
            <input
              type="checkbox"
              checked={visibleOnly}
              onChange={(event) => onVisibleOnlyChange(event.target.checked)}
              className="mt-0.5 accent-[#0f766e]"
            />
            {t("export.visibleOnly")}
          </label>
        )}
        <button
          type="button"
          onClick={onExportVectors}
          disabled={Boolean(busy)}
          className="mt-3 flex min-h-10 w-full items-center justify-center gap-2 rounded-xl bg-[#173f38] text-xs font-semibold text-white disabled:opacity-50"
        >
          <Database size={14} />
          {busy === "vectors"
            ? t("export.creating")
            : t("export.saveLayer")}
        </button>
      </section>

      <section className="rounded-2xl border border-[#dce4e1] p-3.5">
        <div className="flex items-start gap-2.5">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-[#fff4d9] text-[#a76509]">
            <Tags size={15} />
          </span>
          <div>
            <h3 className="text-sm font-bold text-[#2d3a36]">
              {t("export.annotations")}
            </h3>
            <p className="mt-0.5 text-[11px] leading-4 text-[#7a8783]">
              {t("export.annotationsHelp")}
            </p>
          </div>
        </div>
        <select
          value={annotationFormat}
          onChange={(event) =>
            onAnnotationFormatChange(event.target.value as VectorFormat)
          }
          className="input-control mt-3 text-xs"
        >
          <option value="gpkg">{t("export.gpkgRecommended")}</option>
          <option value="geojson">GeoJSON</option>
        </select>
        <button
          type="button"
          onClick={onExportAnnotations}
          disabled={Boolean(busy)}
          className="mt-3 flex min-h-10 w-full items-center justify-center gap-2 rounded-xl border border-[#c9d7d2] bg-white text-xs font-semibold text-[#31504a] disabled:opacity-50"
        >
          <Download size={14} />
          {busy === "annotations"
            ? t("export.creating")
            : t("export.saveAnnotations")}
        </button>
      </section>
    </>
  );
}
