import { describe, expect, it } from "vitest";
import { accumulateGsTileWorkerTiming, emptyGsTileDecodeBreakdown } from "./decode-telemetry";

describe("GSTile decode breakdown", () => {
  it("starts each cut with independent zero counters", () => {
    const first = emptyGsTileDecodeBreakdown();
    first.outputCopyMs = 8;
    const next = emptyGsTileDecodeBreakdown();
    expect(Object.values(next).every((value) => value === 0)).toBe(true);
    expect(next).not.toBe(first);
  });

  it("sums task durations without double-counting compute inside round-trip", () => {
    const total = emptyGsTileDecodeBreakdown();
    const timing = { queueMs: 2, inputCopyMs: 3, inputCopyBytes: 96, roundTripMs: 11, computeMs: 5 };
    accumulateGsTileWorkerTiming(total, timing);
    accumulateGsTileWorkerTiming(total, timing);
    expect(total).toEqual({
      workerTasks: 2, queueMs: 4, inputCopyMs: 6, inputCopyBytes: 192,
      roundTripMs: 22, computeMs: 10, outputCopyMs: 0, outputCopyBytes: 0,
      assemblyWorkerMs: 0, assemblyAdmissionMs: 0, assemblyTransferMs: 0, assemblyBytes: 0, assemblyPeakBytes: 0, assemblyPeakTasks: 0,
    });
    expect(timing.computeMs).toBe(5);
  });
});
