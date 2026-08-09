"use client";

import {
  CheckCircle2,
  Download,
  FolderOpen,
  Image as ImageIcon,
  Info,
} from "lucide-react";
import { useState } from "react";
import {
  downloadMapExport,
  getRasterExportPath,
  getVectorExportPath,
} from "../../lib/api";
import { useI18n } from "../../lib/i18n/provider";
import ExportCrsSelector, {
  type ExportCrsChoice,
} from "./ExportCrsSelector";
import VectorExportCards, {
  type VectorFormat,
  type VectorScope,
} from "./VectorExportCards";

type RasterFormat = "cog" | "geotiff";

interface ExportPanelProps {
  missionId: string;
  hasDepth: boolean;
  visibleRunIds: string[];
}

const VECTOR_TYPES = {
  gpkg: {
    description: "GeoPackage QGIS",
    accept: { "application/geopackage+sqlite3": [".gpkg"] },
  },
  geojson: {
    description: "GeoJSON",
    accept: { "application/geo+json": [".geojson"] },
  },
};
const TIFF_TYPE = {
  description: "GeoTIFF",
  accept: { "image/tiff": [".tif", ".tiff"] },
};

export default function ExportPanel({
  missionId,
  hasDepth,
  visibleRunIds,
}: ExportPanelProps) {
  const { t } = useI18n();
  const [rasterLayer, setRasterLayer] = useState<"ortho" | "depth">("ortho");
  const [rasterFormat, setRasterFormat] = useState<RasterFormat>("cog");
  const [vectorFormat, setVectorFormat] = useState<VectorFormat>("gpkg");
  const [vectorScope, setVectorScope] = useState<VectorScope>("all");
  const [annotationFormat, setAnnotationFormat] =
    useState<VectorFormat>("gpkg");
  const [visibleOnly, setVisibleOnly] = useState(false);
  const [exportCrs, setExportCrs] = useState<ExportCrsChoice>({
    request: "raster",
    label: "EPSG:4326",
    valid: true,
  });
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const safeMission = missionId.replace(/[^A-Za-z0-9_.-]/g, "_");
  const runDownload = async (
    id: string,
    path: string,
    filename: string,
    fileType: { description: string; accept: Record<string, string[]> },
  ) => {
    setBusy(id);
    setError("");
    setMessage("");
    try {
      const result = await downloadMapExport(path, filename, fileType);
      if (result === "saved") {
        setMessage(t("export.saved", { filename }));
      } else if (result === "download") {
        setMessage(
          t("export.downloadStarted"),
        );
      }
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : t("export.failed"),
      );
    } finally {
      setBusy("");
    }
  };
  const exportRaster = () => {
    const filename =
      rasterFormat === "cog"
        ? `${safeMission}_${rasterLayer}.cog.tif`
        : `${safeMission}_${rasterLayer}.tif`;
    return runDownload(
      "raster",
      getRasterExportPath(missionId, rasterLayer, rasterFormat),
      filename,
      TIFF_TYPE,
    );
  };
  const exportVectors = () => {
    const extension = vectorFormat === "gpkg" ? "gpkg" : "geojson";
    if (vectorFormat === "gpkg" && !exportCrs.valid) {
      setError(t("export.invalidEpsg"));
      return Promise.resolve();
    }
    if (visibleOnly && visibleRunIds.length === 0) {
      setError(t("export.noVisibleAnalysis"));
      return Promise.resolve();
    }
    const runIds = visibleOnly ? visibleRunIds : [];
    const crs =
      vectorFormat === "geojson" ? "EPSG:4326" : exportCrs.request;
    const crsSlug =
      vectorFormat === "geojson"
        ? "epsg4326"
        : exportCrs.label.toLowerCase().replace(/[^a-z0-9]+/g, "");
    return runDownload(
      "vectors",
      getVectorExportPath(
        missionId,
        vectorFormat,
        vectorScope,
        runIds,
        crs,
      ),
      `${safeMission}_vectors_${vectorScope}_${crsSlug}.${extension}`,
      VECTOR_TYPES[vectorFormat],
    );
  };

  const exportAnnotations = () => {
    const extension = annotationFormat === "gpkg" ? "gpkg" : "geojson";
    if (annotationFormat === "gpkg" && !exportCrs.valid) {
      setError(t("export.invalidEpsg"));
      return Promise.resolve();
    }
    const crs =
      annotationFormat === "geojson" ? "EPSG:4326" : exportCrs.request;
    const crsSlug =
      annotationFormat === "geojson"
        ? "epsg4326"
        : exportCrs.label.toLowerCase().replace(/[^a-z0-9]+/g, "");
    return runDownload(
      "annotations",
      getVectorExportPath(missionId, annotationFormat, "manual", [], crs),
      `${safeMission}_annotations_${crsSlug}.${extension}`,
      VECTOR_TYPES[annotationFormat],
    );
  };
  return (
    <div className="space-y-3">
      <div className="rounded-2xl border border-[#cfe0da] bg-[#f3faf7] p-3">
        <div className="flex gap-2">
          <FolderOpen size={15} className="mt-0.5 shrink-0 text-[#0f766e]" />
          <div>
            <div className="text-xs font-bold text-[#31504a]">
              {t("export.destination")}
            </div>
            <p className="mt-1 text-[11px] leading-5 text-[#61746e]">
              {t("export.destinationHelp")}
            </p>
          </div>
        </div>
      </div>

      {(message || error) && (
        <div
          className={`flex items-start gap-2 rounded-xl p-3 text-xs ${
            error
              ? "bg-rose-50 text-rose-700"
              : "bg-emerald-50 text-emerald-700"
          }`}
        >
          {error ? <Info size={14} /> : <CheckCircle2 size={14} />}
          <span>{error || message}</span>
        </div>
      )}

      <ExportCrsSelector missionId={missionId} onChange={setExportCrs} />

      <section className="rounded-2xl border border-[#dce4e1] p-3.5">
        <div className="flex items-start gap-2.5">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-[#e8f5f1] text-[#0f766e]">
            <ImageIcon size={15} />
          </span>
          <div>
            <h3 className="text-sm font-bold text-[#2d3a36]">GeoTIFF</h3>
            <p className="mt-0.5 text-[11px] leading-4 text-[#7a8783]">
              {t("export.geotiffHelp")}
            </p>
          </div>
        </div>
        <div className="mt-3 grid grid-cols-2 gap-2">
          <select
            value={rasterLayer}
            onChange={(event) =>
              setRasterLayer(event.target.value as "ortho" | "depth")
            }
            className="input-control text-xs"
          >
            <option value="ortho">{t("export.orthomosaic")}</option>
            {hasDepth && <option value="depth">{t("export.elevation")}</option>}
          </select>
          <select
            value={rasterFormat}
            onChange={(event) =>
              setRasterFormat(event.target.value as RasterFormat)
            }
            className="input-control text-xs"
          >
            <option value="cog">{t("export.optimizedCog")}</option>
            <option value="geotiff">GeoTIFF</option>
          </select>
        </div>
        <button
          type="button"
          onClick={() => void exportRaster()}
          disabled={Boolean(busy)}
          className="mt-3 flex min-h-10 w-full items-center justify-center gap-2 rounded-xl bg-[#173f38] text-xs font-semibold text-white disabled:opacity-50"
        >
          <Download size={14} />
          {busy === "raster" ? t("export.preparing") : t("export.saveRaster")}
        </button>
      </section>

      <VectorExportCards
        vectorFormat={vectorFormat}
        vectorScope={vectorScope}
        annotationFormat={annotationFormat}
        visibleOnly={visibleOnly}
        visibleRunIds={visibleRunIds}
        busy={busy}
        onVectorFormatChange={setVectorFormat}
        onVectorScopeChange={setVectorScope}
        onAnnotationFormatChange={setAnnotationFormat}
        onVisibleOnlyChange={setVisibleOnly}
        onExportVectors={() => void exportVectors()}
        onExportAnnotations={() => void exportAnnotations()}
      />

      <div className="rounded-xl bg-[#f7f9f8] p-3 text-[10px] leading-5 text-[#72807b]">
        <strong>{t("export.qgis")}</strong> {t("export.qgisHelp")}
      </div>
    </div>
  );
}
