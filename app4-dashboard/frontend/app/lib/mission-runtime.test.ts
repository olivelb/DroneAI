import { describe, expect, it } from "vitest";

import {
  mergeMissionSnapshots,
  missionSummaryFromDetail,
  summaryLogMessages,
} from "./mission-runtime";
import type { MissionDetail } from "./types";
import type { MissionSummary } from "./types";

const mission = (
  updatedAt: number,
  logs: MissionSummary["logs"] = [],
): MissionSummary => ({
  vol_id: "mission-1",
  services: {},
  logs,
  updated_at: updatedAt,
  overall_status: "processing",
});

describe("mission runtime reconciliation", () => {
  it("does not replace a newer WebSocket snapshot with older polling data", () => {
    const live = mission(20);
    const summary = mission(10);

    expect(
      mergeMissionSnapshots({ "mission-1": live }, { "mission-1": summary }),
    ).toEqual({ "mission-1": live });
  });

  it("clears the console when the selected mission has no logs", () => {
    expect(summaryLogMessages(mission(10))).toEqual([]);
    expect(
      summaryLogMessages(
        mission(10, [{ message: "iteration 2000", step: "GAUSS" }]),
      ),
    ).toEqual(["iteration 2000"]);
  });

  it("maps only the selected mission detail into live stage state", () => {
    const detail: MissionDetail = {
      vol_id: "selected-mission",
      owner_subject: "operator",
      status: "processing",
      current_step: "GAUSSIAN_TRAINING · EXECUTING",
      progress: 30,
      pipeline: "modern",
      quality_profile: "normal-v1",
      attempt_count: 1,
      overall_status: "processing",
      is_stale: false,
      parameters: { ai_backend: "sam3" },
      attempts: [0],
      stage_runs: [
        {
          run_id: "run-1",
          stage: "gaussian_training",
          attempt: 0,
          status: "running",
          progress: 50,
        },
      ],
      phases: {},
      heartbeat: { delayed: false },
      logs: [
        {
          service: "STAGE",
          step: "gaussian_training",
          status: "processing",
          message: "training started",
        },
      ],
      products: [],
    };

    const selected = missionSummaryFromDetail(detail);

    expect(selected.vol_id).toBe("selected-mission");
    expect(selected.progress).toBe(30);
    expect(selected.stage_runs?.[0].stage).toBe("gaussian_training");
    expect(summaryLogMessages(selected)).toEqual(["training started"]);
  });
});
