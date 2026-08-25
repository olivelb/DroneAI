import { describe, expect, it } from "vitest";
import {
  calculateMergedArenaBounds,
  mergedArenaActiveSpans,
  mergedArenaCullIntervals,
  mergeMergedArenaBounds,
  planLinearTextureCopies,
  planMergedArenaSlots,
  type MergedArenaBounds,
  type MergedArenaSlot,
} from "./merged-arena";

describe("merged GSTile arena culling intervals", () => {
  it("keeps per-node bounds across sorted and fragmented spans", () => {
    const aBounds: MergedArenaBounds = {
      min: [-2, -1, 0],
      max: [2, 3, 4],
    };
    const bBounds: MergedArenaBounds = {
      min: [10, 11, 12],
      max: [13, 14, 15],
    };
    const slots = new Map<string, MergedArenaSlot>([
      [
        "b",
        {
          spans: [
            { offset: 7, count: 2 },
            { offset: 2, count: 1 },
          ],
          count: 3,
        },
      ],
      ["a", { spans: [{ offset: 3, count: 4 }], count: 4 }],
    ]);

    expect(
      mergedArenaCullIntervals(
        slots,
        new Map([
          ["a", aBounds],
          ["b", bBounds],
        ]),
        1,
      ),
    ).toEqual([
      { start: 2, count: 1, bounds: bBounds },
      { start: 3, count: 4, bounds: aBounds },
      { start: 7, count: 2, bounds: bBounds },
    ]);
  });

  it("groups bounded contiguous spans without crossing arena gaps", () => {
    const bounds = new Map<string, MergedArenaBounds>([
      ["a", { min: [0, 0, 0], max: [1, 1, 1] }],
      ["b", { min: [2, 2, 2], max: [3, 3, 3] }],
      ["c", { min: [10, 10, 10], max: [11, 11, 11] }],
    ]);
    expect(
      mergedArenaCullIntervals(
        new Map([
          ["a", { spans: [{ offset: 0, count: 2 }], count: 2 }],
          ["b", { spans: [{ offset: 2, count: 3 }], count: 3 }],
          ["c", { spans: [{ offset: 7, count: 2 }], count: 2 }],
        ]),
        bounds,
        2,
      ),
    ).toEqual([
      {
        start: 0,
        count: 5,
        bounds: { min: [0, 0, 0], max: [3, 3, 3] },
      },
      {
        start: 7,
        count: 2,
        bounds: { min: [10, 10, 10], max: [11, 11, 11] },
      },
    ]);
  });

  it("fails closed on missing, invalid or overlapping culling metadata", () => {
    const slots = new Map<string, MergedArenaSlot>([
      ["a", { spans: [{ offset: 0, count: 2 }], count: 2 }],
    ]);
    expect(() => mergedArenaCullIntervals(slots, new Map())).toThrow(
      "are missing",
    );
    expect(() =>
      mergedArenaCullIntervals(
        slots,
        new Map([["a", { min: [1, 0, 0], max: [0, 1, 1] }]]),
      ),
    ).toThrow("are invalid");
    expect(() =>
      mergedArenaCullIntervals(
        new Map([
          ["a", { spans: [{ offset: 0, count: 2 }], count: 2 }],
          ["b", { spans: [{ offset: 1, count: 2 }], count: 2 }],
        ]),
        new Map([
          ["a", { min: [0, 0, 0], max: [1, 1, 1] }],
          ["b", { min: [0, 0, 0], max: [1, 1, 1] }],
        ]),
      ),
    ).toThrow("overlap");
    expect(() =>
      mergedArenaCullIntervals(
        new Map([
          ["a", { spans: [{ offset: -1, count: 2 }], count: 2 }],
        ]),
        new Map([["a", { min: [0, 0, 0], max: [1, 1, 1] }]]),
      ),
    ).toThrow("interval is invalid");
  });
});

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

    const interleaved = calculateMergedArenaBounds(
      [new Float32Array(0), new Float32Array(0), new Float32Array(0)],
      [
        new Float32Array([0, Math.log(2)]),
        new Float32Array([0, 0]),
        new Float32Array([0, 0]),
      ],
      1,
      1,
      new Float32Array([0, 1, 2, 10, 20, 30]),
    );
    expect(interleaved).toEqual(second);
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
    entries.map(([id, offset, count]) => [
      id,
      { spans: [{ offset, count }], count },
    ]),
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
      ["a", { spans: [{ offset: 0, count: 3 }], count: 3 }],
      ["b", { spans: [{ offset: 7, count: 3 }], count: 3 }],
      ["new", { spans: [{ offset: 3, count: 4 }], count: 4 }],
    ]);
    expect(plan.reusedNodeIds).toEqual(["a", "b"]);
    expect(plan.addedNodeIds).toEqual(["new"]);
    expect(plan.removedNodeIds).toEqual(["removed"]);
  });

  it("splits new nodes across fragmented free spans without moving residents", () => {
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
      ["a", { spans: [{ offset: 0, count: 3 }], count: 3 }],
      ["b", { spans: [{ offset: 4, count: 3 }], count: 3 }],
      [
        "new",
        {
          spans: [
            { offset: 3, count: 1 },
            { offset: 7, count: 3 },
          ],
          count: 4,
        },
      ],
    ]);
    expect(plan.reusedNodeIds).toEqual(["a", "b"]);
    expect(mergedArenaActiveSpans(plan.slots)).toEqual([
      { offset: 0, count: 10 },
    ]);
  });

  it("preserves split residents through repeated full-capacity transitions", () => {
    const selections = [
      ["a", "b", "c", "d", "e"],
      ["a", "c", "e", "f", "f"],
      ["a", "e", "f", "f", "g"],
      ["a", "e", "g", "h", "h"],
    ].map((ids) => {
      const counts = new Map<string, number>();
      for (const id of ids) counts.set(id, (counts.get(id) ?? 0) + 3);
      return [...counts].map(([id, count]) => ({ id, count }));
    });
    let slots = new Map<string, MergedArenaSlot>();
    for (const selected of selections) {
      const previousSlots = slots;
      const plan = planMergedArenaSlots(15, previousSlots, selected);
      for (const node of selected) {
        if (previousSlots.has(node.id)) {
          expect(plan.slots.get(node.id)).toEqual(previousSlots.get(node.id));
        }
      }
      expect(mergedArenaActiveSpans(plan.slots)).toEqual([
        { offset: 0, count: 15 },
      ]);
      expect(plan.usedSplats).toBe(15);
      slots = plan.slots;
    }
    expect(slots.get("h")?.spans).toEqual([
      { offset: 3, count: 3 },
      { offset: 9, count: 3 },
    ]);
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
    expect(() =>
      planMergedArenaSlots(
        5,
        new Map([
          [
            "a",
            {
              spans: [
                { offset: 0, count: 1 },
                { offset: 3, count: 1 },
              ],
              count: 3,
            },
          ],
        ]),
        [],
      ),
    ).toThrow("does not match");
  });
});
