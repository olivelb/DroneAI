"use client";

import type { Feature } from "geojson";
import type { MessageKey } from "../../lib/i18n/catalog";
import { useI18n } from "../../lib/i18n/provider";
import type { AnalysisRun } from "../../lib/types";
import type { FeatureBulkAction } from "../../lib/types";
import { geometryBounds } from "./workspace-config";

interface SearchPanelProps {
  source: string;
  runId: string;
  reviewed: string;
  deleted: string;
  analyses: AnalysisRun[];
  results: Feature[];
  selectedIds: string[];
  onSourceChange: (source: string) => void;
  onRunChange: (runId: string) => void;
  onReviewedChange: (reviewed: string) => void;
  onDeletedChange: (deleted: string) => void;
  onSelectionChange: (featureIds: string[]) => void;
  onBulkAction: (action: FeatureBulkAction) => void;
  onSearch: () => void;
  onFeatureSelect: (feature: Feature) => void;
  onFocus: (bounds: [number, number, number, number]) => void;
}

export default function SearchPanel({
  source,
  runId,
  reviewed,
  deleted,
  analyses,
  results,
  selectedIds,
  onSourceChange,
  onRunChange,
  onReviewedChange,
  onDeletedChange,
  onSelectionChange,
  onBulkAction,
  onSearch,
  onFeatureSelect,
  onFocus,
}: SearchPanelProps) {
  const { t } = useI18n();
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-2">
        <select
          value={source}
          onChange={(event) => onSourceChange(event.target.value)}
          className="input-control text-xs"
        >
          <option value="">{t("search.allSources")}</option>
          <option value="pipeline">{t("search.initialPipeline")}</option>
          <option value="manual">{t("search.manual")}</option>
          <option value="ai">{t("search.persistedAi")}</option>
        </select>
        <select
          value={deleted}
          onChange={(event) => onDeletedChange(event.target.value)}
          className="input-control text-xs"
        >
          <option value="false">{t("search.activeObjects")}</option>
          <option value="true">{t("search.withdrawnObjects")}</option>
        </select>
        <select
          value={runId}
          onChange={(event) => onRunChange(event.target.value)}
          className="input-control text-xs"
        >
          <option value="">{t("search.allAnalyses")}</option>
          {analyses
            .filter((run) => run.persist_results)
            .map((run) => (
              <option key={run.run_id} value={run.run_id}>
                {run.name}
              </option>
            ))}
        </select>
        <select
          value={reviewed}
          onChange={(event) => onReviewedChange(event.target.value)}
          className="input-control text-xs"
        >
          <option value="">{t("search.allReviewStates")}</option>
          <option value="true">{t("search.reviewed")}</option>
          <option value="false">{t("search.unreviewed")}</option>
        </select>
      </div>
      <button
        type="button"
        onClick={onSearch}
        className="min-h-10 w-full rounded-xl bg-[#173f38] text-xs font-semibold text-white"
      >
        {t("search.applyFilters")}
      </button>
      <p className="text-xs text-[#82908b]">
        {t("search.results", { count: results.length })}
      </p>
      {selectedIds.length > 0 && (
        <div className="grid grid-cols-2 gap-2 rounded-xl bg-[#edf9f6] p-2">
          <div className="col-span-2 text-xs font-semibold text-[#0f766e]">
            {t("search.selected", { count: selectedIds.length })}
          </div>
          {(["review", "unreview", "delete", "restore"] as FeatureBulkAction[]).map(
            (action) => (
              <button
                key={action}
                type="button"
                onClick={() => onBulkAction(action)}
                className="min-h-9 rounded-lg border border-[#badbd3] bg-white text-[11px]"
              >
                {t(`search.bulk.${action}` as MessageKey)}
              </button>
            ),
          )}
        </div>
      )}
      {results.map((feature, index) => {
        const featureId = String(feature.properties?.feature_id ?? "");
        const selectable =
          !!featureId && ["manual", "ai"].includes(String(feature.properties?.source));
        const selected = selectedIds.includes(featureId);
        return (
        <div
          key={String(feature.id ?? index)}
          className="flex w-full gap-2 rounded-xl border border-[#dce4e1] p-3 hover:border-[#77bdae]"
        >
          {selectable && (
            <input
              type="checkbox"
              checked={selected}
              onChange={() => onSelectionChange(
                selected
                  ? selectedIds.filter((id) => id !== featureId)
                  : [...selectedIds, featureId],
              )}
              aria-label={t("search.selectObject")}
              className="mt-1 accent-[#0f766e]"
            />
          )}
          <button
            type="button"
            onClick={() => {
              if (feature.geometry) onFocus(geometryBounds(feature.geometry));
              onFeatureSelect(feature);
            }}
            className="min-w-0 flex-1 text-left"
          >
          <div className="truncate text-sm font-semibold text-[#34413d]">
            {String(
              feature.properties?.name ||
                feature.properties?.class_name ||
                t("search.object"),
            )}
          </div>
          <div className="mt-1 truncate text-xs text-[#82908b]">
            {String(
              feature.properties?.description ||
                feature.properties?.source ||
                "",
            )}
          </div>
          {feature.properties?.reviewed === true && (
            <div className="mt-1 text-[10px] font-semibold text-emerald-700">
              {t("search.reviewed")}
            </div>
          )}
          </button>
        </div>
      );})}
    </div>
  );
}
