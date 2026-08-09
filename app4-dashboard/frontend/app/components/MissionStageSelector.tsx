"use client";

import React from "react";
import type { MessageKey } from "../lib/i18n/catalog";
import { useI18n } from "../lib/i18n/provider";
import { MISSION_STAGE_ORDER, toggleMissionStage } from "../lib/stage-selection";
import { useStore } from "../lib/store";
import type { MissionStageId } from "../lib/types";

const LABELS: Record<MissionStageId, MessageKey> = {
  reconstruction: "stages.reconstruction",
  gaussian_training: "stages.gaussianTraining",
  gaussian_filtering: "stages.gaussianFiltering",
  rasterization: "stages.rasterization",
  detection: "stages.detection",
};

export default function MissionStageSelector() {
  const { t } = useI18n();
  const { selectedStages, setSelectedStages } = useStore();

  return (
    <fieldset>
      <legend className="text-sm font-medium text-gray-600">{t("stages.title")}</legend>
      <p className="mt-1 text-xs text-[#7a8783]">{t("stages.help")}</p>
      <div className="mt-3 grid gap-2 sm:grid-cols-2">
        {MISSION_STAGE_ORDER.map((stage, index) => (
          <label key={stage} className="flex min-h-11 items-center gap-2 rounded-xl border border-[#dce4e1] bg-[#f8faf9] px-3 text-xs font-semibold text-[#46534f]">
            <input
              type="checkbox"
              checked={selectedStages.includes(stage)}
              onChange={(event) => setSelectedStages(
                toggleMissionStage(selectedStages, stage, event.target.checked),
              )}
              className="h-4 w-4 accent-[#0f766e]"
            />
            <span>{index + 1}. {t(LABELS[stage])}</span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}
