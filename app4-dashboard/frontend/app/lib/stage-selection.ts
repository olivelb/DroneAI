import type { MissionStageId } from "./types";

export const MISSION_STAGE_ORDER: MissionStageId[] = [
  "reconstruction",
  "gaussian_training",
  "gaussian_filtering",
  "rasterization",
  "detection",
];

export const toggleMissionStage = (
  selected: MissionStageId[],
  stage: MissionStageId,
  enabled: boolean,
): MissionStageId[] => {
  const index = MISSION_STAGE_ORDER.indexOf(stage);
  const next = new Set(selected);
  if (enabled) {
    MISSION_STAGE_ORDER.slice(0, index + 1).forEach((candidate) => next.add(candidate));
  } else {
    MISSION_STAGE_ORDER.slice(index).forEach((candidate) => next.delete(candidate));
  }
  return MISSION_STAGE_ORDER.filter((candidate) => next.has(candidate));
};
