/** Elapsed durations use each realm's own clock; never subtract realm timestamps. */
export type GsTileWorkerDecodeTiming = {
  queueMs: number;
  inputCopyMs: number;
  inputCopyBytes: number;
  roundTripMs: number;
  computeMs: number;
};

/** Per-cut sums, not wall time. Compute is nested inside Worker round-trip time. */
export type GsTileDecodeBreakdown = GsTileWorkerDecodeTiming & {
  workerTasks: number;
  outputCopyMs: number;
  outputCopyBytes: number;
};

export const emptyGsTileDecodeBreakdown = (): GsTileDecodeBreakdown => ({
  workerTasks: 0,
  queueMs: 0,
  inputCopyMs: 0,
  inputCopyBytes: 0,
  roundTripMs: 0,
  computeMs: 0,
  outputCopyMs: 0,
  outputCopyBytes: 0,
});

export const accumulateGsTileWorkerTiming = (
  total: GsTileDecodeBreakdown,
  timing: GsTileWorkerDecodeTiming,
) => {
  total.workerTasks += 1;
  total.queueMs += timing.queueMs;
  total.inputCopyMs += timing.inputCopyMs;
  total.inputCopyBytes += timing.inputCopyBytes;
  total.roundTripMs += timing.roundTripMs;
  total.computeMs += timing.computeMs;
};
