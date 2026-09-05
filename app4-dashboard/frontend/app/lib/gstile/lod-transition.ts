import type { GsTileNode } from "./contracts";

export type LodStep = { ids: string[]; added: number; complete: boolean };
/** A bounded sequence of atomic parent/children replacements. No overlapping cut. */
export function nextLodCut(nodes: readonly GsTileNode[], current: readonly string[],
  target: readonly string[], budget: number, maxAdded = 65536): LodStep {
  const byId = new Map(nodes.map(n => [n.id, n]));
  const parents = new Map<string, string>();
  for (const n of nodes) for (const child of n.children ?? []) parents.set(child, n.id);
  const ancestors = (id: string) => {
    const result = new Set<string>();
    for (let p = parents.get(id); p !== undefined; p = parents.get(p)) result.add(p);
    return result;
  };
  const targetSet = new Set(target), targetAncestors = new Set<string>();
  for (const id of target) for (const p of ancestors(id)) targetAncestors.add(p);
  const relevant = (id: string) => targetSet.has(id) || targetAncestors.has(id) ||
    [...ancestors(id)].some(p => targetSet.has(p));
  const currentCount = current.reduce((sum, id) => {
    const node = byId.get(id), tile = node?.tile ?? node?.lodTile;
    if (!tile) throw new Error("Missing LOD representation " + id);
    return sum + tile.recordCount;
  }, 0);
  if ((!current.length || currentCount > budget) && target.length) {
    let root = target[0];
    while (parents.has(root)) root = parents.get(root)!;
    const node = byId.get(root), tile = node?.tile ?? node?.lodTile;
    if (!tile || tile.recordCount > budget) throw new Error("Root exceeds LOD budget");
    return { ids: [root], added: tile.recordCount, complete: target.length === 1 && target[0] === root };
  }
  const cut = new Set(current.filter(relevant));
  const count = (id: string) => {
    const node = byId.get(id), tile = node?.tile ?? node?.lodTile;
    if (!tile) throw new Error("Missing LOD representation " + id);
    return tile.recordCount;
  };
  let used = [...cut].reduce((n,id) => n + count(id), 0), added = 0;
  const replace = (remove: string[], insert: string[]) => {
    const cost = insert.reduce((n,id) => n + count(id), 0);
    const next = used - remove.reduce((n,id) => n + count(id), 0) + cost;
    if (next > budget || (added > 0 && added + cost > maxAdded)) return false;
    for (const id of remove) cut.delete(id);
    for (const id of insert) cut.add(id);
    added += cost; used = next;
    return true;
  };
  // Coarsen first, freeing room for refinement. Disjoint newly visible branches also enter here.
  for (const id of target) {
    if (cut.has(id) || [...ancestors(id)].some(p => cut.has(p))) continue;
    const remove = [...cut].filter(p => ancestors(p).has(id));
    replace(remove, [id]);
  }
  for (const id of [...cut]) {
    if (!targetAncestors.has(id)) continue;
    const children = (byId.get(id)?.children ?? []).filter(relevant);
    if (children.length && !replace([id], children)) {
      const descendants = target.filter(t => ancestors(t).has(id));
      if (descendants.length) replace([id], descendants);
    }
  }
  const ids = [...cut].sort();
  return { ids, added, complete: ids.length === target.length && ids.every(id => targetSet.has(id)) };
}
