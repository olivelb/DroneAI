import { describe, expect, it } from "vitest";
import {
  calculateMergedArenaBounds,
  mergeMergedArenaBounds,
  planLinearTextureCopies,
  planMergedArenaSlots,
  type MergedArenaSlot,
} from "./merged-arena";

describe("merged GSTile arena texture copies", () => {
  it("splits linear ranges at both source and destination row boundaries", () => {
    expect(planLinearTextureCopies(4, 3, 3, 3, 4, 2, 7)).toEqual([
      {
        sourceX: 3,
        sourceY: 0,
        destX: 2,
        destY: 0,
        width: 1,
        height: 1,
      },
      {
        sourceX: 0,
        sourceY: 1,
        destX: 0,
        destY: 1,
        width: 3,
        height: 1,
      },
      {
        sourceX: 3,
        sourceY: 1,
        destX: 0,
        destY: 2,
        width: 1,
        height: 1,
      },
      {
        sourceX: 0,
        sourceY: 2,
        destX: 1,
        destY: 2,
        width: 2,
        height: 1,
      },
    ]);
  });

  it("rejects invalid dimensions and out-of-bounds ranges", () => {
    expect(() => planLinearTextureCopies(0, 1, 0, 1, 1, 0, 1)).toThrow(
      "positive safe integer",
    );
    expect(() => planLinearTextureCopies(2, 2, 3, 2, 2, 0, 2)).toThrow(
      "escapes",
    );
    expect(() => planLinearTextureCopies(2, 2, 0, 2, 2, 4, 1)).toThrow(
      "escapes",
    );
  });
});

describe("merged GSTile arena bounds", () => {
  it("matches the conservative PlayCanvas Gaussian support and merges nodes", () => {
    const first = calculateMergedArenaBounds(
      [
        new Float32Array([0, 10]),
        new Float32Array([1, 20]),
        new Float32Array([2, 30]),
      ],
      [
        new Float32Array([0, 0]),
        new Float32Array([0, 0]),
        new Float32Array([0, 0]),
      ],
      0,
      1,
    );
    const second = calculateMergedArenaBounds(
      [
        new Float32Array([0, 10]),
        new Float32Array([1, 20]),
        new Float32Array([2, 30]),
      ],
      [
        new Float32Array([0, Math.log(2)]),
        new Float32Array([0, 0]),
        new Float32Array([0, 0]),
      ],
      1,
      1,
    );

    expect(first).toEqual({ min: [-2, -1, 0], max: [2, 3, 4] });
    second.min.forEach((value, axis) =>
      expect(value).toBeCloseTo([6, 16, 26][axis]),
    );
    second.max.forEach((value, axis) =>
      expect(value).toBeCloseTo([14, 24, 34][axis]),
    );
    const merged = mergeMergedArenaBounds([first, second]);
    expect(merged.min).toEqual([-2, -1, 0]);
    merged.max.forEach((value, axis) =>
      expect(value).toBeCloseTo([14, 24, 34][axis]),
    );
  });

  it("rejects empty or non-finite bounds inputs", () => {
    expect(() => mergeMergedArenaBounds([])).toThrow("must not be empty");
    expect(() =>
      calculateMergedArenaBounds(
        [new Float32Array([NaN]), new Float32Array([0]), new Float32Array([0])],
        [new Float32Array([0]), new Float32Array([0]), new Float32Array([0])],
        0,
        1,
      ),
    ).toThrow("no finite Gaussian");
  });
});

const previous = (entries: Array<[string, number, number]>) =>
  new Map<string, MergedArenaSlot>(
    entries.map(([id, offset, count]) => [id, { offset, count }]),
  );

describe("merged GSTile arena slot planning", () => {
  it("retains stable slots and fills released holes deterministically", () => {
    const plan = planMergedArenaSlots(
      12,
      previous([
        ["a", 0, 3],
        ["removed", 3, 4],
        ["b", 7, 3],
      ]),
      [
        { id: "a", count: 3 },
        { id: "b", count: 3 },
        { id: "new", count: 4 },
      ],
    );

    expect([...plan.slots]).toEqual([
      ["a", { offset: 0, count: 3 }],
      ["b", { offset: 7, count: 3 }],
      ["new", { offset: 3, count: 4 }],
    ]);
    expect(plan.reusedNodeIds).toEqual(["a", "b"]);
    expect(plan.addedNodeIds).toEqual(["new"]);
    expect(plan.removedNodeIds).toEqual(["removed"]);
    expect(plan.movedNodeIds).toEqual([]);
    expect(plan.compacted).toBe(false);
  });

  it("compacts explicitly when total free capacity is fragmented", () => {
    const plan = planMergedArenaSlots(
      10,
      previous([
        ["a", 0, 3],
        ["removed-a", 3, 1],
        ["b", 4, 3],
        ["removed-b", 7, 3],
      ]),
      [
        { id: "a", count: 3 },
        { id: "b", count: 3 },
        { id: "new", count: 4 },
      ],
    );

    expect([...plan.slots]).toEqual([
      ["a", { offset: 0, count: 3 }],
      ["b", { offset: 3, count: 3 }],
      ["new", { offset: 6, count: 4 }],
    ]);
    expect(plan.compacted).toBe(true);
    expect(plan.reusedNodeIds).toEqual(["a"]);
    expect(plan.movedNodeIds).toEqual(["b"]);
  });

  it("rejects overlap, overflow, duplicate IDs and count drift", () => {
    expect(() =>
      planMergedArenaSlots(
        10,
        previous([
          ["a", 0, 6],
          ["b", 5, 2],
        ]),
        [],
      ),
    ).toThrow("overlap");
    expect(() =>
      planMergedArenaSlots(5, new Map(), [{ id: "a", count: 6 }]),
    ).toThrow("exceeds");
    expect(() =>
      planMergedArenaSlots(5, new Map(), [
        { id: "a", count: 1 },
        { id: "a", count: 1 },
      ]),
    ).toThrow("unique");
    expect(() =>
      planMergedArenaSlots(5, previous([["a", 0, 2]]), [{ id: "a", count: 3 }]),
    ).toThrow("changed record count");
  });
});
