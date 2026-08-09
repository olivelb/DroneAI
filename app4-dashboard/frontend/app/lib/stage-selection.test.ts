import { describe, expect, it } from "vitest";
import { toggleMissionStage } from "./stage-selection";

const stages = [
  "reconstruction",
  "gaussian_training",
  "gaussian_filtering",
  "rasterization",
  "detection",
] as const;

describe("mission stage selection", () => {
  it("selects every required ancestor", () => {
    expect(toggleMissionStage([], "detection", true)).toEqual(stages);
  });

  it("removes dependants when an input stage is removed", () => {
    expect(toggleMissionStage([...stages], "gaussian_training", false)).toEqual([
      "reconstruction",
    ]);
  });
});
