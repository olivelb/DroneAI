export type MergedArenaNode = {
  id: string;
  count: number;
};

export type MergedArenaSpan = {
  offset: number;
  count: number;
};

export type MergedArenaSlot = {
  spans: MergedArenaSpan[];
  count: number;
};

export type MergedArenaPlan = {
  slots: Map<string, MergedArenaSlot>;
  addedNodeIds: string[];
  reusedNodeIds: string[];
  removedNodeIds: string[];
  usedSplats: number;
};

export type LinearTextureCopy = {
  sourceX: number;
  sourceY: number;
  destX: number;
  destY: number;
  width: number;
  height: number;
};

export type MergedArenaBounds = {
  min: [number, number, number];
  max: [number, number, number];
};

const validatePositiveSafeInteger = (value: number, label: string) => {
  if (!Number.isSafeInteger(value) || value < 1) {
    throw new Error(`${label} must be a positive safe integer`);
  }
};

/**
 * Copy a linear range exactly, batching vertical strips when widths match.
 *
 * PlayCanvas packs every stream in a 2D texture. Source staging resources and
 * the persistent arena can have different widths, so a single linear range
 * must be split whenever either texture reaches the end of a row.
 */
export const planLinearTextureCopies = (
  sourceWidth: number,
  sourceHeight: number,
  sourceOffset: number,
  destinationWidth: number,
  destinationHeight: number,
  destinationOffset: number,
  count: number,
): LinearTextureCopy[] => {
  validatePositiveSafeInteger(sourceWidth, "GSTile source texture width");
  validatePositiveSafeInteger(sourceHeight, "GSTile source texture height");
  validatePositiveSafeInteger(
    destinationWidth,
    "GSTile destination texture width",
  );
  validatePositiveSafeInteger(
    destinationHeight,
    "GSTile destination texture height",
  );
  validatePositiveSafeInteger(count, "GSTile texture copy count");
  if (
    !Number.isSafeInteger(sourceOffset) ||
    !Number.isSafeInteger(destinationOffset) ||
    sourceOffset < 0 ||
    destinationOffset < 0 ||
    !Number.isSafeInteger(sourceOffset + count) ||
    !Number.isSafeInteger(destinationOffset + count) ||
    sourceOffset + count > sourceWidth * sourceHeight ||
    destinationOffset + count > destinationWidth * destinationHeight
  ) {
    throw new Error("GSTile texture copy escapes its source or destination");
  }

  const copies: LinearTextureCopy[] = [];
  let source = sourceOffset;
  let destination = destinationOffset;
  let remaining = count;
  if (sourceWidth === destinationWidth) {
    const partial = () => {
      const sourceX = source % sourceWidth;
      const destX = destination % destinationWidth;
      const width = Math.min(sourceWidth - sourceX, destinationWidth - destX, remaining);
      copies.push({ sourceX, sourceY: Math.floor(source / sourceWidth), destX,
        destY: Math.floor(destination / destinationWidth), width, height: 1 });
      source += width;
      destination += width;
      remaining -= width;
    };
    while (remaining > 0 && source % sourceWidth !== 0) partial();
    const rows = Math.floor(remaining / sourceWidth);
    if (rows > 0) {
      const destX = destination % destinationWidth;
      const sourceY = source / sourceWidth;
      const destY = Math.floor(destination / destinationWidth);
      // Two disjoint strips account for a destination row wrap. No gap is written.
      copies.push({ sourceX: 0, sourceY, destX, destY, width: sourceWidth - destX, height: rows });
      if (destX !== 0) copies.push({ sourceX: sourceWidth - destX, sourceY,
        destX: 0, destY: destY + 1, width: destX, height: rows });
      source += rows * sourceWidth;
      destination += rows * sourceWidth;
      remaining -= rows * sourceWidth;
    }
    while (remaining > 0) partial();
    return copies;
  }
  while (remaining > 0) {
    const sourceX = source % sourceWidth;
    const destinationX = destination % destinationWidth;
    const width = Math.min(
      sourceWidth - sourceX,
      destinationWidth - destinationX,
      remaining,
    );
    copies.push({
      sourceX,
      sourceY: Math.floor(source / sourceWidth),
      destX: destinationX,
      destY: Math.floor(destination / destinationWidth),
      width,
      height: 1,
    });
    source += width;
    destination += width;
    remaining -= width;
  }
  return copies;
};

/** Match PlayCanvas GSplatData.calcAabb for one decoded node range. */
export const calculateMergedArenaBounds = (
  position: readonly Float32Array[],
  logScale: readonly Float32Array[],
  offset: number,
  count: number,
  centerStream: Float32Array | null = null,
): MergedArenaBounds => {
  const totalCount = logScale[0]?.length ?? 0;
  if (
    position.length !== 3 ||
    logScale.length !== 3 ||
    logScale.some((column) => column.length !== totalCount) ||
    (centerStream
      ? centerStream.length !== totalCount * 3 ||
        position.some((column) => column.length !== 0)
      : position.some((column) => column.length !== totalCount)) ||
    !Number.isSafeInteger(offset) ||
    offset < 0 ||
    !Number.isSafeInteger(count) ||
    count < 1 ||
    offset + count > totalCount
  ) {
    throw new Error("GSTile arena AABB range or columns are invalid");
  }
  const minimum: [number, number, number] = [Infinity, Infinity, Infinity];
  const maximum: [number, number, number] = [-Infinity, -Infinity, -Infinity];
  let valid = false;
  for (let record = offset; record < offset + count; record += 1) {
    const centerOffset = record * 3;
    const x = centerStream ? centerStream[centerOffset] : position[0][record];
    const y = centerStream
      ? centerStream[centerOffset + 1]
      : position[1][record];
    const z = centerStream
      ? centerStream[centerOffset + 2]
      : position[2][record];
    const scale = Math.max(
      logScale[0][record],
      logScale[1][record],
      logScale[2][record],
    );
    if (![x, y, z, scale].every(Number.isFinite)) continue;
    const radius = 2 * Math.exp(scale);
    const center = [x, y, z] as const;
    for (let axis = 0; axis < 3; axis += 1) {
      minimum[axis] = Math.min(minimum[axis], center[axis] - radius);
      maximum[axis] = Math.max(maximum[axis], center[axis] + radius);
    }
    valid = true;
  }
  if (!valid) throw new Error("GSTile arena node has no finite Gaussian");
  return { min: minimum, max: maximum };
};

export const mergeMergedArenaBounds = (
  bounds: Iterable<MergedArenaBounds>,
): MergedArenaBounds => {
  const minimum: [number, number, number] = [Infinity, Infinity, Infinity];
  const maximum: [number, number, number] = [-Infinity, -Infinity, -Infinity];
  let count = 0;
  for (const bound of bounds) {
    for (let axis = 0; axis < 3; axis += 1) {
      if (
        !Number.isFinite(bound.min[axis]) ||
        !Number.isFinite(bound.max[axis]) ||
        bound.min[axis] > bound.max[axis]
      ) {
        throw new Error("GSTile arena AABB is invalid");
      }
      minimum[axis] = Math.min(minimum[axis], bound.min[axis]);
      maximum[axis] = Math.max(maximum[axis], bound.max[axis]);
    }
    count += 1;
  }
  if (count === 0) throw new Error("GSTile arena AABB set must not be empty");
  return { min: minimum, max: maximum };
};

const validatePreviousSlots = (
  previous: ReadonlyMap<string, MergedArenaSlot>,
  capacity: number,
) => {
  const ordered: Array<readonly [string, MergedArenaSpan]> = [];
  for (const [id, slot] of previous) {
    if (!id) throw new Error("GSTile arena node ID must not be empty");
    validatePositiveSafeInteger(slot.count, `GSTile arena slot ${id} count`);
    if (slot.spans.length === 0) {
      throw new Error(`GSTile arena slot ${id} must contain a span`);
    }
    let spanCount = 0;
    for (const span of slot.spans) {
      validatePositiveSafeInteger(
        span.count,
        `GSTile arena slot ${id} span count`,
      );
      if (!Number.isSafeInteger(span.offset) || span.offset < 0) {
        throw new Error("GSTile arena spans have an invalid offset");
      }
      spanCount += span.count;
      ordered.push([id, span]);
    }
    if (!Number.isSafeInteger(spanCount) || spanCount !== slot.count) {
      throw new Error(`GSTile arena slot ${id} span count does not match`);
    }
  }
  ordered.sort(
    ([leftId, left], [rightId, right]) =>
      left.offset - right.offset || leftId.localeCompare(rightId),
  );
  let end = 0;
  for (const [, span] of ordered) {
    if (span.offset < end) {
      throw new Error("GSTile arena spans overlap");
    }
    end = span.offset + span.count;
    if (!Number.isSafeInteger(end) || end > capacity) {
      throw new Error("GSTile arena span escapes its capacity");
    }
  }
};

/** Flatten and coalesce the occupied arena spans for PlayCanvas intervals. */
export const mergedArenaActiveSpans = (
  slots: ReadonlyMap<string, MergedArenaSlot>,
): MergedArenaSpan[] => {
  const ordered = [...slots.values()]
    .flatMap((slot) => slot.spans)
    .sort((left, right) => left.offset - right.offset);
  const merged: MergedArenaSpan[] = [];
  for (const span of ordered) {
    const previous = merged.at(-1);
    if (!previous || previous.offset + previous.count < span.offset) {
      merged.push({ ...span });
    } else if (previous.offset + previous.count === span.offset) {
      previous.count += span.count;
    } else {
      throw new Error("GSTile arena spans overlap");
    }
  }
  return merged;
};

/**
 * Plan stable slots for one monolithic GPU arena.
 *
 * Existing spans are always retained. New nodes can consume multiple free
 * spans, avoiding a full GPU arena rebuild when total capacity is sufficient
 * but no single free range can hold the node.
 */
export const planMergedArenaSlots = (
  capacity: number,
  previous: ReadonlyMap<string, MergedArenaSlot>,
  selected: readonly MergedArenaNode[],
): MergedArenaPlan => {
  validatePositiveSafeInteger(capacity, "GSTile merged arena capacity");
  validatePreviousSlots(previous, capacity);
  const selectedIds = new Set<string>();
  let usedSplats = 0;
  for (const node of selected) {
    if (!node.id || selectedIds.has(node.id)) {
      throw new Error(
        "GSTile merged arena node IDs must be unique and non-empty",
      );
    }
    validatePositiveSafeInteger(
      node.count,
      `GSTile arena node ${node.id} count`,
    );
    const old = previous.get(node.id);
    if (old && old.count !== node.count) {
      throw new Error(`GSTile arena node ${node.id} changed record count`);
    }
    selectedIds.add(node.id);
    usedSplats += node.count;
  }
  if (!Number.isSafeInteger(usedSplats) || usedSplats > capacity) {
    throw new Error("GSTile merged arena target exceeds its capacity");
  }

  const retained = selected
    .map((node) => [node.id, previous.get(node.id)] as const)
    .filter(
      (entry): entry is readonly [string, MergedArenaSlot] =>
        entry[1] !== undefined,
    );
  const occupied = retained
    .flatMap(([id, slot]) => slot.spans.map((span) => [id, span] as const))
    .sort(
      ([leftId, left], [rightId, right]) =>
        left.offset - right.offset || leftId.localeCompare(rightId),
    );
  const free: MergedArenaSpan[] = [];
  let cursor = 0;
  for (const [, span] of occupied) {
    if (span.offset > cursor) {
      free.push({ offset: cursor, count: span.offset - cursor });
    }
    cursor = span.offset + span.count;
  }
  if (cursor < capacity)
    free.push({ offset: cursor, count: capacity - cursor });

  const slots = new Map<string, MergedArenaSlot>(
    retained.map(([id, slot]) => [
      id,
      { count: slot.count, spans: slot.spans.map((span) => ({ ...span })) },
    ]),
  );
  for (const node of selected) {
    if (slots.has(node.id)) continue;
    const spans: MergedArenaSpan[] = [];
    let remaining = node.count;
    while (remaining > 0) {
      const range = free[0];
      if (!range) {
        throw new Error("GSTile merged arena free-space invariant failed");
      }
      const count = Math.min(remaining, range.count);
      spans.push({ offset: range.offset, count });
      range.offset += count;
      range.count -= count;
      remaining -= count;
      if (range.count === 0) free.shift();
    }
    slots.set(node.id, { spans, count: node.count });
  }

  const reusedNodeIds: string[] = [];
  const addedNodeIds: string[] = [];
  for (const node of selected) {
    const old = previous.get(node.id);
    const next = slots.get(node.id);
    if (!next) throw new Error(`GSTile arena slot ${node.id} is missing`);
    if (!old) addedNodeIds.push(node.id);
    else reusedNodeIds.push(node.id);
  }

  return {
    slots,
    addedNodeIds,
    reusedNodeIds,
    removedNodeIds: [...previous.keys()].filter((id) => !selectedIds.has(id)),
    usedSplats,
  };
};
