import { describe, expect, it } from "vitest";

import {
  mergeMissionSnapshots,
  summaryLogMessages,
} from "./mission-runtime";
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

  it("does not clear the live console when summary logs are absent", () => {
    expect(summaryLogMessages(mission(10))).toBeNull();
    expect(
      summaryLogMessages(
        mission(10, [{ message: "iteration 2000", step: "GAUSS" }]),
      ),
    ).toEqual(["iteration 2000"]);
  });
});
