import type { GsTilePlayCanvasColumns } from "./decode";

/** Copy a contiguous range into the persistent arena's CPU sort centers. */
export const copyGsTileCenters = (
  columns: Pick<GsTilePlayCanvasColumns, "count" | "centerStream" | "position">,
  sourceOffset: number,
  destination: Float32Array,
  destinationOffset: number,
  count: number,
) => {
  const sourceEnd = sourceOffset + count;
  const destinationEnd = destinationOffset + count;
  if (
    !Number.isSafeInteger(sourceOffset) ||
    sourceOffset < 0 ||
    !Number.isSafeInteger(destinationOffset) ||
    destinationOffset < 0 ||
    !Number.isSafeInteger(count) ||
    count < 0 ||
    sourceEnd > columns.count ||
    destinationEnd > Math.floor(destination.length / 3)
  ) {
    throw new Error("GSTile center copy range is invalid");
  }
  if (columns.centerStream) {
    if (columns.centerStream.length < sourceEnd * 3) {
      throw new Error("GSTile packed center source is truncated");
    }
    // subarray is a view, not another data allocation. TypedArray.set also
    // preserves Float32 bits and handles overlapping packed ranges.
    destination.set(
      columns.centerStream.subarray(sourceOffset * 3, sourceEnd * 3),
      destinationOffset * 3,
    );
    return;
  }
  if (
    columns.position.length !== 3 ||
    columns.position.some((axis) => axis.length < sourceEnd)
  ) {
    throw new Error("GSTile position columns are truncated");
  }
  for (let record = 0; record < count; record += 1) {
    const source = sourceOffset + record;
    const target = (destinationOffset + record) * 3;
    destination[target] = columns.position[0][source];
    destination[target + 1] = columns.position[1][source];
    destination[target + 2] = columns.position[2][source];
  }
};
