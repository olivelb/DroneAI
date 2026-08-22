import type { GsTileManifest, GsTileNode, Vec3 } from "./contracts";

export type GsTileLodSelectionOptions = {
  cameraPosition: Vec3;
  verticalFovRadians: number;
  viewportHeight: number;
  maximumResidentGaussians: number;
  maximumProjectedErrorPixels: number;
};

export type GsTileLodSelection = {
  selectedNodeIds: string[];
  residentGaussians: number;
  maximumSelectedErrorPixels: number;
};

type Candidate = {
  node: GsTileNode;
  errorPixels: number;
};

const representationCount = (node: GsTileNode) =>
  node.tile?.recordCount ?? node.lodTile?.recordCount ?? 0;

const distanceToBounds = (position: Vec3, node: GsTileNode) => {
  let squared = 0;
  for (let axis = 0; axis < 3; axis += 1) {
    const distance = Math.max(
      node.bounds.min[axis] - position[axis],
      0,
      position[axis] - node.bounds.max[axis],
    );
    squared += distance * distance;
  }
  return Math.max(Math.sqrt(squared), 1e-6);
};

const projectedErrorPixels = (
  node: GsTileNode,
  options: GsTileLodSelectionOptions,
) => {
  const geometricError = node.geometricError ?? 0;
  if (geometricError <= 0 || !node.children) return 0;
  const focalPixels =
    options.viewportHeight / (2 * Math.tan(options.verticalFovRadians / 2));
  return (geometricError * focalPixels) / distanceToBounds(options.cameraPosition, node);
};

const higherPriority = (left: Candidate, right: Candidate) =>
  left.errorPixels > right.errorPixels ||
  (left.errorPixels === right.errorPixels && left.node.id < right.node.id);

class CandidateHeap {
  readonly #items: Candidate[] = [];

  push(candidate: Candidate) {
    this.#items.push(candidate);
    let index = this.#items.length - 1;
    while (index > 0) {
      const parent = Math.floor((index - 1) / 2);
      if (higherPriority(this.#items[parent], candidate)) break;
      this.#items[index] = this.#items[parent];
      index = parent;
    }
    this.#items[index] = candidate;
  }

  pop() {
    const first = this.#items[0];
    const last = this.#items.pop();
    if (!first || !last || this.#items.length === 0) return first;
    let index = 0;
    while (true) {
      const left = index * 2 + 1;
      const right = left + 1;
      if (left >= this.#items.length) break;
      let child = left;
      if (
        right < this.#items.length &&
        higherPriority(this.#items[right], this.#items[left])
      ) {
        child = right;
      }
      if (higherPriority(last, this.#items[child])) break;
      this.#items[index] = this.#items[child];
      index = child;
    }
    this.#items[index] = last;
    return first;
  }
}

export const selectGsTileLod = (
  manifest: GsTileManifest,
  options: GsTileLodSelectionOptions,
): GsTileLodSelection => {
  if (
    !Number.isFinite(options.verticalFovRadians) ||
    options.verticalFovRadians <= 0 ||
    options.verticalFovRadians >= Math.PI ||
    !Number.isFinite(options.viewportHeight) ||
    options.viewportHeight <= 0 ||
    !Number.isSafeInteger(options.maximumResidentGaussians) ||
    options.maximumResidentGaussians < 1 ||
    !Number.isFinite(options.maximumProjectedErrorPixels) ||
    options.maximumProjectedErrorPixels <= 0
  ) {
    throw new Error("Invalid GSTile LOD selection options");
  }

  const nodes = new Map(manifest.nodes.map((node) => [node.id, node]));
  const root = nodes.get(manifest.root);
  if (!root || representationCount(root) < 1) {
    throw new Error("GSTile LOD root has no renderable representation");
  }
  const selected = new Set([root.id]);
  let residentGaussians = representationCount(root);
  if (residentGaussians > options.maximumResidentGaussians) {
    throw new Error("GSTile root proxy exceeds the resident splat budget");
  }

  const candidates = new CandidateHeap();
  const enqueue = (node: GsTileNode) => {
    if (!node.children) return;
    candidates.push({
      node,
      errorPixels: projectedErrorPixels(node, options),
    });
  };
  enqueue(root);

  while (true) {
    const candidate = candidates.pop();
    if (!candidate || candidate.errorPixels <= options.maximumProjectedErrorPixels) {
      break;
    }
    const children = candidate.node.children?.map((id) => nodes.get(id));
    if (!children || children.some((node) => !node || representationCount(node) < 1)) {
      throw new Error(`GSTile node ${candidate.node.id} has invalid LOD children`);
    }
    const typedChildren = children as GsTileNode[];
    const childrenCount = typedChildren.reduce(
      (total, node) => total + representationCount(node),
      0,
    );
    const nextCount =
      residentGaussians - representationCount(candidate.node) + childrenCount;
    if (nextCount > options.maximumResidentGaussians) continue;

    selected.delete(candidate.node.id);
    for (const child of typedChildren) {
      selected.add(child.id);
      enqueue(child);
    }
    residentGaussians = nextCount;
  }

  const selectedNodes = manifest.nodes.filter((node) => selected.has(node.id));
  return {
    selectedNodeIds: selectedNodes.map((node) => node.id),
    residentGaussians,
    maximumSelectedErrorPixels: selectedNodes.reduce(
      (maximum, node) => Math.max(maximum, projectedErrorPixels(node, options)),
      0,
    ),
  };
};
