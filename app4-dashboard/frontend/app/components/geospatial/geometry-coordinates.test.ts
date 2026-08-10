import { describe, expect, it } from "vitest";
import {
  editableCoordinateVertices,
  updateEditableCoordinateVertex,
} from "./geometry-coordinates";

describe("editable geometry coordinates", () => {
  it("updates a point in WGS84 order", () => {
    const geometry = updateEditableCoordinateVertex(
      { type: "Point", coordinates: [1, 44] },
      0,
      1.25,
      44.5,
    );
    expect(geometry).toEqual({ type: "Point", coordinates: [1.25, 44.5] });
  });

  it("keeps a polygon exterior ring closed", () => {
    const geometry = {
      type: "Polygon" as const,
      coordinates: [[[1, 44], [2, 44], [2, 45], [1, 44]]],
    };
    expect(editableCoordinateVertices(geometry)).toHaveLength(3);
    expect(updateEditableCoordinateVertex(geometry, 0, 1.1, 44.1)).toEqual({
      type: "Polygon",
      coordinates: [[[1.1, 44.1], [2, 44], [2, 45], [1.1, 44.1]]],
    });
  });

  it("rejects invalid geographic coordinates", () => {
    expect(() =>
      updateEditableCoordinateVertex(
        { type: "Point", coordinates: [1, 44] },
        0,
        181,
        44,
      ),
    ).toThrow(/longitude/);
  });
});
