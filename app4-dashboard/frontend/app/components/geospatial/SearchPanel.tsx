"use client";

import type { Feature } from "geojson";
import { useI18n } from "../../lib/i18n/provider";
import type { AnalysisRun } from "../../lib/types";
import { geometryBounds } from "./workspace-config";

interface SearchPanelProps {
  source: string;
  runId: string;
  analyses: AnalysisRun[];
  results: Feature[];
  onSourceChange: (source: string) => void;
  onRunChange: (runId: string) => void;
  onSearch: () => void;
  onFeatureSelect: (feature: Feature) => void;
  onFocus: (bounds: [number, number, number, number]) => void;
}

export default function SearchPanel({
  source,
  runId,
  analyses,
  results,
  onSourceChange,
  onRunChange,
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
          <option value="legacy">{t("search.initialPipeline")}</option>
          <option value="manual">{t("search.manual")}</option>
          <option value="ai">{t("search.persistedAi")}</option>
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
      {results.map((feature, index) => (
        <button
          type="button"
          key={String(feature.id ?? index)}
          onClick={() => {
            if (feature.geometry) {
              onFocus(geometryBounds(feature.geometry));
            }
            onFeatureSelect(feature);
          }}
          className="w-full rounded-xl border border-[#dce4e1] p-3 text-left hover:border-[#77bdae]"
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
        </button>
      ))}
    </div>
  );
}
