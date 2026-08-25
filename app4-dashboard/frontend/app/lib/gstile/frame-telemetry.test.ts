import { describe, expect, it } from "vitest";
import {
  captureGsTileGpuPassTelemetry,
  retainGsTileFrameTelemetry,
} from "./playcanvas-backend";

describe("GSTile frame telemetry", () => {
  it("retains the last rendered sample while render-on-demand is idle", () => {
    const rendered = {
      frameCpuMs: 4.25,
      frameGpuMs: 7.5,
      workBufferUploadPercent: 12,
      gpuPasses: [{ name: "radix-sort", durationMs: 6 }],
    };

    expect(
      retainGsTileFrameTelemetry(
        rendered,
        {
          frameCpuMs: 0,
          frameGpuMs: 0,
          workBufferUploadPercent: 0,
          gpuPasses: [],
        },
        false,
      ),
    ).toBe(rendered);
  });

  it("publishes the new sample after a real render", () => {
    const current = {
      frameCpuMs: 3,
      frameGpuMs: null,
      workBufferUploadPercent: 0,
      gpuPasses: [],
    };

    expect(
      retainGsTileFrameTelemetry(
        {
          frameCpuMs: 9,
          frameGpuMs: 11,
          workBufferUploadPercent: 100,
          gpuPasses: [{ name: "projection", durationMs: 5 }],
        },
        current,
        true,
      ),
    ).toBe(current);
  });

  it("captures named finite GPU passes in engine order", () => {
    expect(
      captureGsTileGpuPassTelemetry(
        new Map([
          ["projection", 3.5],
          ["", 1],
          ["radix-sort", 8.25],
          ["invalid", Number.NaN],
        ]),
      ),
    ).toEqual([
      { name: "projection", durationMs: 3.5 },
      { name: "radix-sort", durationMs: 8.25 },
    ]);
  });
});
