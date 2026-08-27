import { describe, expect, it } from "vitest";
import { copyGsTileCenters } from "./center-stream";
import { allocateGsTilePlayCanvasColumns } from "./decode";

describe("GSTile arena CPU centers", () => {
  it("copies packed centers even when the PLY position columns are empty", () => {
    const columns = allocateGsTilePlayCanvasColumns(4, { centerBounds: true });
    columns.centerStream!.set([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]);
    expect(columns.position.every((axis) => axis.length === 0)).toBe(true);
    const destination = new Float32Array(18).fill(-99);
    copyGsTileCenters(columns, 1, destination, 2, 2);
    expect([...destination]).toEqual([
      -99, -99, -99, -99, -99, -99, 4, 5, 6, 7, 8, 9, -99, -99, -99, -99, -99, -99,
    ]);
  });

  it("keeps the legacy unpacked path with nonzero offsets", () => {
    const columns = allocateGsTilePlayCanvasColumns(3);
    columns.position[0].set([1, 4, 7]);
    columns.position[1].set([2, 5, 8]);
    columns.position[2].set([3, 6, 9]);
    const destination = new Float32Array(12).fill(-99);
    copyGsTileCenters(columns, 1, destination, 1, 2);
    expect([...destination]).toEqual([-99, -99, -99, 4, 5, 6, 7, 8, 9, -99, -99, -99]);
  });

  it("preserves all Float32 bits, including signed zero and NaN payloads", () => {
    const columns = allocateGsTilePlayCanvasColumns(2, { centerBounds: true });
    const bits = new Uint32Array(columns.centerStream!.buffer);
    bits.set([0x80000000, 0x7fc01234, 0xff800000, 0x00000001, 0x3f800001, 0x7f800000]);
    const destination = new Float32Array(6);
    copyGsTileCenters(columns, 0, destination, 0, 2);
    expect(new Uint32Array(destination.buffer)).toEqual(bits);
  });

  it.each(["forward", "backward"])("supports %s overlapping packed ranges", (direction) => {
    const columns = allocateGsTilePlayCanvasColumns(4, { centerBounds: true });
    columns.centerStream!.set([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]);
    const forward = direction === "forward";
    copyGsTileCenters(columns, forward ? 0 : 1, columns.centerStream!, forward ? 1 : 0, 3);
    expect([...columns.centerStream!]).toEqual(forward
      ? [1, 2, 3, 1, 2, 3, 4, 5, 6, 7, 8, 9]
      : [4, 5, 6, 7, 8, 9, 10, 11, 12, 10, 11, 12]);
  });

  it("copies fragmented destination spans without touching their holes", () => {
    const columns = allocateGsTilePlayCanvasColumns(4, { centerBounds: true });
    columns.centerStream!.set([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]);
    const destination = new Float32Array(21).fill(-99);
    copyGsTileCenters(columns, 0, destination, 1, 1);
    copyGsTileCenters(columns, 1, destination, 4, 3);
    expect([...destination]).toEqual([
      -99, -99, -99, 1, 2, 3, -99, -99, -99, -99, -99, -99, 4, 5, 6, 7, 8, 9, 10, 11, 12,
    ]);
  });

  it.each([
    [-1, 0, 1], [0, -1, 1], [0, 0, -1], [0.5, 0, 1],
    [0, 0.5, 1], [0, 0, 0.5], [NaN, 0, 1], [0, Infinity, 1],
    [0, 0, Number.MAX_SAFE_INTEGER], [2, 0, 2], [0, 3, 1],
  ])("rejects invalid ranges (%s, %s, %s) before writing", (source, target, count) => {
    const columns = allocateGsTilePlayCanvasColumns(3, { centerBounds: true });
    const destination = new Float32Array(9).fill(-99);
    expect(() => copyGsTileCenters(columns, source, destination, target, count)).toThrow();
    expect([...destination]).toEqual(Array(9).fill(-99));
  });

  it.each([true, false])("rejects a truncated source atomically (packed=%s)", (packed) => {
    const columns = allocateGsTilePlayCanvasColumns(3, { centerBounds: packed });
    if (packed) columns.centerStream = new Float32Array(5);
    else columns.position[2] = new Float32Array(1);
    const destination = new Float32Array(9).fill(-99);
    expect(() => copyGsTileCenters(columns, 0, destination, 0, 3)).toThrow();
    expect([...destination]).toEqual(Array(9).fill(-99));
  });

  it("accepts an empty range at the end without changing data", () => {
    const columns = allocateGsTilePlayCanvasColumns(3, { centerBounds: true });
    const destination = new Float32Array(9).fill(-99);
    copyGsTileCenters(columns, 3, destination, 3, 0);
    expect([...destination]).toEqual(Array(9).fill(-99));
  });
});
