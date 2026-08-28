import type { Geometry } from "geojson";
import { describe, expect, it } from "vitest";

import {
  geometryBounds,
  retainKnownRunIds,
  rasterProductFiles,
  splitTags,
  statusTone,
} from "./workspace-config";

describe("workspace configuration helpers", () => {
  it("discovers raster layers from the latest immutable product only", () => {
    expect(rasterProductFiles([
      {
        kind: "raster_product_workspace", artifact_id: "previous",
        metadata: { ortho_file: "orthomosaic.tif", height_file: "orthomosaic.height.tif" },
      },
      {
        kind: "raster_product_workspace", artifact_id: "latest",
        metadata: { ortho_file: "facade_orthophoto.tif", height_file: "facade_orthophoto.height.tif" },
      },
      { kind: "detection_workspace", artifact_id: "detections" },
    ])).toEqual(["facade_orthophoto.tif", "facade_orthophoto.height.tif"]);
  });

  it("does not discover layers from historical root-file products", () => {
    expect(rasterProductFiles([{ kind: "orthomosaic", s3_key: "missions/old/orthomosaic.tif" }])).toEqual([]);
    expect(rasterProductFiles()).toEqual([]);
  });

  it("does not invent missing or malformed product paths", () => {
    expect(rasterProductFiles([{
      kind: "raster_product_workspace", artifact_id: "latest",
      metadata: { ortho_file: "", height_file: 12 },
    }])).toEqual([]);
  });

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
