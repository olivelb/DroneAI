import { describe, expect, it } from "vitest";
import { patchPlayCanvasIncrementalWorldSource } from "./patch-playcanvas-incremental-world.mjs";

const original = `before
\t\tif (this._placementSetChanged) {
\t\t\tnewState.fullRebuild = true;
\t\t}
after`;

describe("PlayCanvas incremental GSplat world patch", () => {
  it("keeps stable allocations during placement churn", () => {
    const patched = patchPlayCanvasIncrementalWorldSource(original);
    expect(patched).not.toContain("newState.fullRebuild = true");
    expect(patched).toContain("allocation diff");
  });

  it("is idempotent", () => {
    const patched = patchPlayCanvasIncrementalWorldSource(original);
    expect(patchPlayCanvasIncrementalWorldSource(patched)).toBe(patched);
  });

  it("rejects an unaudited source shape", () => {
    expect(() => patchPlayCanvasIncrementalWorldSource("unknown")).toThrow(
      /Unexpected PlayCanvas placement rebuild state/,
    );
  });
});
