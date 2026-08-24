export type MergedArenaNode = {
  id: string;
  count: number;
};

export type MergedArenaSlot = {
  offset: number;
  count: number;
};

export type MergedArenaPlan = {
  slots: Map<string, MergedArenaSlot>;
  addedNodeIds: string[];
  reusedNodeIds: string[];
  movedNodeIds: string[];
  removedNodeIds: string[];
  compacted: boolean;
  usedSplats: number;
};

export type LinearTextureCopy = {
  sourceX: number;
  sourceY: number;
  destX: number;
  destY: number;
  width: number;
  height: 1;
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
 * Split one linear splat range into row-bounded GPU texture copies.
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
    sourceOffset + count > sourceWidth * sourceHeight ||
    destinationOffset + count > destinationWidth * destinationHeight
  ) {
    throw new Error("GSTile texture copy escapes its source or destination");
  }

  const copies: LinearTextureCopy[] = [];
  let source = sourceOffset;
  let destination = destinationOffset;
  let remaining = count;
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
): MergedArenaBounds => {
  if (
    position.length !== 3 ||
    logScale.length !== 3 ||
    position.some((column) => column.length !== logScale[0]?.length) ||
    logScale.some((column) => column.length !== position[0]?.length) ||
    !Number.isSafeInteger(offset) ||
    offset < 0 ||
    !Number.isSafeInteger(count) ||
    count < 1 ||
    offset + count > (position[0]?.length ?? 0)
  ) {
    throw new Error("GSTile arena AABB range or columns are invalid");
  }
  const minimum: [number, number, number] = [Infinity, Infinity, Infinity];
  const maximum: [number, number, number] = [-Infinity, -Infinity, -Infinity];
  let valid = false;
  for (let record = offset; record < offset + count; record += 1) {
    const x = position[0][record];
    const y = position[1][record];
    const z = position[2][record];
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
  const ordered = [...previous].sort(
    ([leftId, left], [rightId, right]) =>
      left.offset - right.offset || leftId.localeCompare(rightId),
  );
  let end = 0;
  for (const [id, slot] of ordered) {
    if (!id) throw new Error("GSTile arena node ID must not be empty");
    validatePositiveSafeInteger(slot.count, `GSTile arena slot ${id} count`);
    if (!Number.isSafeInteger(slot.offset) || slot.offset < end) {
      throw new Error("GSTile arena slots overlap or have an invalid offset");
    }
    end = slot.offset + slot.count;
    if (!Number.isSafeInteger(end) || end > capacity) {
      throw new Error("GSTile arena slot escapes its capacity");
    }
  }
};

const compactPlan = (
  selected: readonly MergedArenaNode[],
  capacity: number,
) => {
  const slots = new Map<string, MergedArenaSlot>();
  let offset = 0;
  for (const node of selected) {
    slots.set(node.id, { offset, count: node.count });
    offset += node.count;
  }
  if (offset > capacity) {
    throw new Error("GSTile merged arena target exceeds its capacity");
  }
  return slots;
};

/**
 * Plan stable slots for one monolithic GPU arena.
 *
 * Existing offsets are retained whenever first-fit can place every new node.
 * Fragmentation triggers one explicit deterministic compaction instead of
 * silently overflowing or producing overlapping GPU copy ranges.
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
  const occupied = retained.sort(
    ([leftId, left], [rightId, right]) =>
      left.offset - right.offset || leftId.localeCompare(rightId),
  );
  const free: MergedArenaSlot[] = [];
  let cursor = 0;
  for (const [, slot] of occupied) {
    if (slot.offset > cursor) {
      free.push({ offset: cursor, count: slot.offset - cursor });
    }
    cursor = slot.offset + slot.count;
  }
  if (cursor < capacity)
    free.push({ offset: cursor, count: capacity - cursor });

  let compacted = false;
  let slots = new Map(retained);
  for (const node of selected) {
    if (slots.has(node.id)) continue;
    const freeIndex = free.findIndex((slot) => slot.count >= node.count);
    if (freeIndex < 0) {
      compacted = true;
      slots = compactPlan(selected, capacity);
      break;
    }
    const range = free[freeIndex];
    slots.set(node.id, { offset: range.offset, count: node.count });
    range.offset += node.count;
    range.count -= node.count;
    if (range.count === 0) free.splice(freeIndex, 1);
  }

  const reusedNodeIds: string[] = [];
  const movedNodeIds: string[] = [];
  const addedNodeIds: string[] = [];
  for (const node of selected) {
    const old = previous.get(node.id);
    const next = slots.get(node.id);
    if (!next) throw new Error(`GSTile arena slot ${node.id} is missing`);
    if (!old) addedNodeIds.push(node.id);
    else if (old.offset === next.offset) reusedNodeIds.push(node.id);
    else movedNodeIds.push(node.id);
  }

  return {
    slots,
    addedNodeIds,
    reusedNodeIds,
    movedNodeIds,
    removedNodeIds: [...previous.keys()].filter((id) => !selectedIds.has(id)),
    compacted,
    usedSplats,
  };
};
