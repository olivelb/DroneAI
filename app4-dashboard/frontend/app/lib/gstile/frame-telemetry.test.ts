import { describe, expect, it } from "vitest";
import { retainGsTileFrameTelemetry } from "./playcanvas-backend";

describe("GSTile frame telemetry", () => {
  it("retains the last rendered sample while render-on-demand is idle", () => {
    const rendered = {
      frameCpuMs: 4.25,
      frameGpuMs: 7.5,
      workBufferUploadPercent: 12,
    };

    expect(
      retainGsTileFrameTelemetry(
        rendered,
        {
          frameCpuMs: 0,
          frameGpuMs: 0,
          workBufferUploadPercent: 0,
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
    };

    expect(
      retainGsTileFrameTelemetry(
        {
          frameCpuMs: 9,
          frameGpuMs: 11,
          workBufferUploadPercent: 100,
        },
        current,
        true,
      ),
    ).toBe(current);
  });
});
