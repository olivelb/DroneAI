import { describe, expect, it } from "vitest";

import { getMapTileUrl } from "./api";

describe("getMapTileUrl", () => {
  it("serializes an RGB recipe with stable global ranges", () => {
    const url = getMapTileUrl("mission 1", "ortho", {
      bands: [3, 2, 1],
      display_ranges: [[0, 255], [10, 240], null],
      palette: "none",
      opacity: 0.75,
      stretch: "global-percentile",
    });

    expect(url).toContain("/maps/mission%201/tiles/ortho/{z}/{x}/{y}.png?");
    expect(url).toContain("bands=3%2C2%2C1");
    expect(url).toContain("display_ranges=0%3A255%2C10%3A240%2Cauto");
    expect(url).toContain("palette=none");
  });

  it("serializes a single-band palette recipe", () => {
    const url = getMapTileUrl("mission-1", "depth", {
      bands: [1],
      display_ranges: [[12.5, 48]],
      palette: "terrain",
      opacity: 1,
      stretch: "fixed",
    });

    expect(url).toContain("bands=1");
    expect(url).toContain("palette=terrain");
    expect(url).toContain("display_ranges=12.5%3A48");
  });
});
