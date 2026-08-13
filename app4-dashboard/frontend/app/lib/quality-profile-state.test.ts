import { describe, expect, it } from "vitest";

import { qualityProfileParameters } from "./quality-profile-state";
import type { ParameterConfigResponse } from "./types";

const candidateSchema = {
  pipelines: { modern: {}, legacy: {} },
  processes: [],
  metadata: {},
  quality_profiles: [
    {
      id: "normal-v4",
      version: 4,
      name: "Normal candidate",
      description: "Projected resident candidate",
      parameters: {
        gs_iterations: "15000",
        gs_cap_max: "3000000",
        gs_resident_partitioning: true,
        gs_initial_scale_policy: "projected-knn",
        gs_initial_max_projected_sigma_pixels: "8.0",
        gs_capacity_targeted_growth: true,
      },
    },
  ],
  quality_profile_default: "normal-v3",
  yolo_models: [],
  sam3: {
    model_id: "sam3",
    model_revision: "test",
    processor_target_size: 1008,
    maximum_source_tile_size: 1024,
    inference_batch_size: 1,
    minimum_vram_gib: 8,
  },
  stage_dag: { version: 1, stages: [] },
} satisfies ParameterConfigResponse;

describe("qualityProfileParameters", () => {
  it("applies the complete projected Normal candidate envelope", () => {
    expect(qualityProfileParameters(candidateSchema, "normal-v4")).toEqual(
      candidateSchema.quality_profiles[0].parameters,
    );
  });

  it("does not partially apply a profile missing from the catalog", () => {
    expect(qualityProfileParameters(candidateSchema, "high-quality-v4")).toEqual({});
  });
});
