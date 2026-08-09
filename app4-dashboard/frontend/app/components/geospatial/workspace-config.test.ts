import type { Geometry } from "geojson";
import { describe, expect, it } from "vitest";

import {
  geometryBounds,
  retainKnownRunIds,
  splitTags,
  statusTone,
} from "./workspace-config";

describe("workspace configuration helpers", () => {
  it("normalizes comma-separated tags", () => {
    expect(splitTags(" inspection, façade, ,urgent ")).toEqual([
      "inspection",
      "façade",
      "urgent",
    ]);
  });

  it("maps terminal statuses to stable visual tones", () => {
    expect(statusTone("completed")).toContain("emerald");
    expect(statusTone("failed")).toContain("rose");
    expect(statusTone("cancelled")).toContain("slate");
    expect(statusTone("processing")).toContain("amber");
  });

  it("does not re-enable an analysis hidden by the operator", () => {
    expect(retainKnownRunIds(["visible"], ["visible", "hidden"])).toEqual([
      "visible",
    ]);
  });

  it("computes bounds across a geometry collection", () => {
    const geometry: Geometry = {
      type: "GeometryCollection",
      geometries: [
        { type: "Point", coordinates: [4, 8] },
        {
          type: "LineString",
          coordinates: [[-2, 3], [6, -1]],
        },
      ],
    };

    expect(geometryBounds(geometry)).toEqual([-2, -1, 6, 8]);
  });

  it("rejects empty geometries instead of returning infinite bounds", () => {
    expect(() => geometryBounds({
      type: "GeometryCollection",
      geometries: [],
    })).toThrow("Geometry has no coordinate positions");
  });
});
