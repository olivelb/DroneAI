"use client";

import {
  Download,
  Eye,
  EyeOff,
  Map as MapIcon,
} from "lucide-react";
import { getFileUrl } from "../../lib/api";
import type { MessageKey } from "../../lib/i18n/catalog";
import { useI18n } from "../../lib/i18n/provider";
import type { AnalysisRun } from "../../lib/types";
import type {
  RasterLayerStyle,
  RasterMetadata,
  RasterStyleRecipe,
} from "../../lib/types";
import RasterStyleControls from "./RasterStyleControls";
import type { ViewerLayer } from "./workspace-config";

interface LayersPanelProps {
  workspacePrefix: string;
  activeLayer: ViewerLayer;
  hasDepth: boolean;
  availableFiles: string[];
  rasterMetadata: RasterMetadata | null;
  rasterStyle: RasterStyleRecipe;
  savedRasterStyles: RasterLayerStyle[];
  rasterStyleName: string;
  savingRasterStyle: boolean;
  showPipeline: boolean;
  showManual: boolean;
  analyses: AnalysisRun[];
  visibleRuns: string[];
  onLayerChange: (layer: ViewerLayer) => void;
  onRasterStyleChange: (style: RasterStyleRecipe) => void;
  onRasterStyleNameChange: (name: string) => void;
  onSavedRasterStyleApply: (style: RasterLayerStyle) => void;
  onRasterStyleSave: () => void;
  onPipelineChange: (visible: boolean) => void;
  onManualChange: (visible: boolean) => void;
  onRunVisibilityChange: (runId: string, visible: boolean) => void;
}

export default function LayersPanel({
  workspacePrefix,
  activeLayer,
  hasDepth,
  availableFiles,
  rasterMetadata,
  rasterStyle,
  savedRasterStyles,
  rasterStyleName,
  savingRasterStyle,
  showPipeline,
  showManual,
  analyses,
  visibleRuns,
  onLayerChange,
  onRasterStyleChange,
  onRasterStyleNameChange,
  onSavedRasterStyleApply,
  onRasterStyleSave,
  onPipelineChange,
  onManualChange,
  onRunVisibilityChange,
}: LayersPanelProps) {
  const { t } = useI18n();
  const hasMapOrthophoto = availableFiles.some((file) =>
    file.endsWith("orthomosaic.tif"),
  );
  const hasFacadeOrthophoto = availableFiles.some((file) =>
    file.endsWith("facade_orthophoto.tif"),
  );
  return (
    <div className="space-y-5">
      <div>
        <div className="eyebrow mb-2">{t("layers.raster")}</div>
        <div className="space-y-2">
          {(["ortho", "depth"] as ViewerLayer[]).map((layer) => {
            const available = layer === "ortho" || hasDepth;
            return (
              <button
                type="button"
                key={layer}
                disabled={!available}
                onClick={() => onLayerChange(layer)}
                className={`flex w-full items-center gap-2 rounded-xl border p-3 text-sm ${
                  activeLayer === layer
                    ? "border-[#68bfae] bg-[#edf9f6] text-[#0f766e]"
                    : "border-[#dce4e1] text-[#5d6965]"
                } disabled:opacity-35`}
              >
                <MapIcon size={15} />
                {layer === "ortho"
                  ? t("layers.orthomosaic")
                  : t("layers.depth")}
              </button>
            );
          })}
        </div>
        <div className="mt-3">
          <RasterStyleControls
            metadata={rasterMetadata}
            recipe={rasterStyle}
            savedStyles={savedRasterStyles}
            styleName={rasterStyleName}
            saving={savingRasterStyle}
            onRecipeChange={onRasterStyleChange}
            onStyleNameChange={onRasterStyleNameChange}
            onSavedStyleApply={onSavedRasterStyleApply}
            onSave={onRasterStyleSave}
          />
        </div>
      </div>

      <div>
        <div className="eyebrow mb-2">{t("layers.vectors")}</div>
        {[
          {
            labelKey: "layers.pipelineDetections" as MessageKey,
            visible: showPipeline,
            toggle: onPipelineChange,
          },
          {
            labelKey: "layers.manualAnnotations" as MessageKey,
            visible: showManual,
            toggle: onManualChange,
          },
        ].map(({ labelKey, visible, toggle }) => (
          <button
            type="button"
            key={labelKey}
            onClick={() => toggle(!visible)}
            className="mb-2 flex w-full items-center gap-2 rounded-xl border border-[#dce4e1] p-3 text-left text-sm text-[#5d6965]"
          >
            {visible ? <Eye size={15} /> : <EyeOff size={15} />}
            {t(labelKey)}
          </button>
        ))}
        {analyses.map((run) => {
          const visible = visibleRuns.includes(run.run_id);
          return (
            <button
              type="button"
              key={run.run_id}
              onClick={() =>
                onRunVisibilityChange(run.run_id, !visible)
              }
              className="mb-2 flex w-full items-center gap-2 rounded-xl border border-[#dce4e1] p-3 text-left text-sm"
            >
              <span
                className="h-3 w-3 rounded-full"
                style={{ backgroundColor: run.color }}
              />
              <span className="min-w-0 flex-1 truncate">{run.name}</span>
              {visible ? <Eye size={14} /> : <EyeOff size={14} />}
            </button>
          );
        })}
      </div>

      <div>
        <div className="eyebrow mb-2">{t("layers.exports")}</div>
        {hasMapOrthophoto && (
          <a
            href={getFileUrl(`${workspacePrefix}/orthomosaic.tif`)}
            className="flex items-center gap-2 rounded-xl border border-[#dce4e1] p-3 text-sm text-[#5d6965]"
          >
            <Download size={14} /> GeoTIFF / COG
          </a>
        )}
        {hasFacadeOrthophoto && (
          <>
            <a
              href={getFileUrl(`${workspacePrefix}/facade_orthophoto.tif`)}
              className="flex items-center gap-2 rounded-xl border border-[#dce4e1] p-3 text-sm text-[#5d6965]"
            >
              <Download size={14} /> {t("layers.facadeOrtho")}
            </a>
            <a
              href={getFileUrl(`${workspacePrefix}/facade_frame.json`)}
              className="mt-2 flex items-center gap-2 rounded-xl border border-[#dce4e1] p-3 text-sm text-[#5d6965]"
            >
              <Download size={14} /> {t("layers.facadeReport")}
            </a>
          </>
        )}
      </div>
    </div>
  );
}
