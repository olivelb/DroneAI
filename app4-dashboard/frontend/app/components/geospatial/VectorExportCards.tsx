"use client";

import { Database, Download, MapPinned, Tags } from "lucide-react";

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
  return (
    <>
      <section className="rounded-2xl border border-[#dce4e1] p-3.5">
        <div className="flex items-start gap-2.5">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-[#eef2fb] text-[#4568b1]">
            <MapPinned size={15} />
          </span>
          <div>
            <h3 className="text-sm font-bold text-[#2d3a36]">
              Couche vectorielle
            </h3>
            <p className="mt-0.5 text-[11px] leading-4 text-[#7a8783]">
              Détections, analyses IA et, au choix, annotations manuelles.
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
            <option value="all">Toutes les sources</option>
            <option value="ai">Analyses IA</option>
            <option value="legacy">Pipeline initial</option>
          </select>
          <select
            value={vectorFormat}
            onChange={(event) =>
              onVectorFormatChange(event.target.value as VectorFormat)
            }
            className="input-control text-xs"
          >
            <option value="gpkg">GeoPackage recommandé</option>
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
            Limiter l’IA aux analyses actuellement visibles
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
            ? "Création du fichier…"
            : "Enregistrer la couche"}
        </button>
      </section>

      <section className="rounded-2xl border border-[#dce4e1] p-3.5">
        <div className="flex items-start gap-2.5">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-[#fff4d9] text-[#a76509]">
            <Tags size={15} />
          </span>
          <div>
            <h3 className="text-sm font-bold text-[#2d3a36]">Annotations</h3>
            <p className="mt-0.5 text-[11px] leading-4 text-[#7a8783]">
              Géométries manuelles, noms, descriptions, tags et couleurs.
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
          <option value="gpkg">GeoPackage recommandé</option>
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
            ? "Création du fichier…"
            : "Enregistrer les annotations"}
        </button>
      </section>
    </>
  );
}
