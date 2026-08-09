import { describe, expect, it } from "vitest";

import {
  missionPhaseStatus,
  overallStatusFor,
  SERVICE_ORDER,
  serviceOrderFor,
} from "./types";

describe("serviceOrderFor", () => {
  it("keeps the complete map pipeline", () => {
    expect(serviceOrderFor({})).toEqual(SERVICE_ORDER);
    expect(serviceOrderFor({
      COLMAP: {
        vol_id: "map-1",
        details: { process: "map", terminal: true },
      },
    })).toEqual(SERVICE_ORDER);
  });

  it("ends a qualified facade after COLMAP", () => {
    expect(serviceOrderFor({
      COLMAP: {
        vol_id: "facade-1",
        details: { process: "facade", terminal: true },
      },
    })).toEqual(["COLMAP"]);
  });
});

describe("overallStatusFor", () => {
  it("treats cancellation as terminal without hiding errors", () => {
    expect(overallStatusFor({
      COLMAP: { vol_id: "cancelled-1", status: "cancelled" },
    })).toBe("cancelled");
    expect(overallStatusFor({
      COLMAP: { vol_id: "failed-1", status: "cancelled" },
      IA: { vol_id: "failed-1", status: "error" },
    })).toBe("error");
  });

  it("keeps incomplete pipelines processing", () => {
    expect(overallStatusFor({
      COLMAP: { vol_id: "map-1", status: "success" },
    })).toBe("processing");
  });
});

describe("missionPhaseStatus", () => {
  it("marks reconstruction complete once DroneGS is active", () => {
    const mission = {
      vol_id: "mission-1",
      services: {
        COLMAP: {
          vol_id: "mission-1",
          step: "GAUSS",
          status: "processing" as const,
          progress: 68,
        },
      },
      logs: [],
      updated_at: 1,
      overall_status: "processing",
    };

    expect(missionPhaseStatus(mission, "reconstruction")).toBe("success");
    expect(missionPhaseStatus(mission, "gaussian")).toBe("processing");
  });
});
