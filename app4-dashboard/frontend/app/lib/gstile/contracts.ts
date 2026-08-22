import {
  ResponseContractError,
  decoder,
  integerValue,
  nonEmptyString,
  numberValue,
  objectWith,
  oneOf,
  arrayOf,
  nullable,
  type Validator,
} from "../contract-decoder";
import { GSTILE_PACK_HEADER_BYTES, GSTILE_RECORD_BYTES } from "./pack";

export const GSTILE_SCHEMA = "droneai-gstile";
export const GSTILE_VERSION = 1;
export const GSTILE_PROFILE = "dronegs-sh3-opacity-sh3-q96";

export type Vec3 = [number, number, number];

export type GsTileQuantization = {
  position: { min: Vec3; max: Vec3 };
  logScale: { min: Vec3; max: Vec3 };
  rotation: { encoding: "snorm16x4" };
  opacityLogit: { min: number; max: number };
  colorDcScale: Vec3;
  colorShScale: number[];
  opacityShScale: number[];
  sourceColorShDegree: number;
  sourceOpacityShDegree: number;
};

export type GsTileNode = {
  id: string;
  bounds: { min: Vec3; max: Vec3 };
  gaussianCount: number;
  children?: [string, string];
  tile?: {
    pack: string;
    byteOffset: number;
    byteLength: number;
    recordCount: number;
    sha256: string;
    quantization: GsTileQuantization;
  };
};

export type GsTilePack = {
  id: string;
  path: string;
  byteLength: number;
  recordCount: number;
  sha256: string;
  payloadCrc32: string;
  byteOffset: number;
};

export type GsTileManifest = {
  schema: typeof GSTILE_SCHEMA;
  version: typeof GSTILE_VERSION;
  profile: typeof GSTILE_PROFILE;
  bundleId: string;
  source: {
    sha256: string;
    gaussianCount: number;
    colorShDegree: number;
    opacityShDegree: number;
    recordBytes: number;
  };
  coordinateFrame: {
    kind: "local" | "projected";
    origin: Vec3;
    crs: string | null;
  };
  root: string;
  nodes: GsTileNode[];
  packs: GsTilePack[];
  statistics: {
    leafCount: number;
    packBytes: number;
    bytesPerGaussian: number;
    lod: string;
  };
};

const finiteVec3: Validator = (value, path) => {
  if (!Array.isArray(value) || value.length !== 3) {
    throw new ResponseContractError("GSTile manifest", path, "3-item tuple");
  }
  value.forEach((item, index) => numberValue(item, `${path}[${index}]`));
};

const fixedNumberArray = (length: number): Validator => (value, path) => {
  if (!Array.isArray(value) || value.length !== length) {
    throw new ResponseContractError(
      "GSTile manifest",
      path,
      `${length}-item number array`,
    );
  }
  value.forEach((item, index) => numberValue(item, `${path}[${index}]`));
};

const childPair: Validator = (value, path) => {
  if (!Array.isArray(value) || value.length !== 2) {
    throw new ResponseContractError(
      "GSTile manifest",
      path,
      "2-item string tuple",
    );
  }
  value.forEach((item, index) => nonEmptyString(item, `${path}[${index}]`));
};

const boundsValidator = objectWith({ min: finiteVec3, max: finiteVec3 });
const quantizationValidator = objectWith({
  position: boundsValidator,
  logScale: boundsValidator,
  rotation: objectWith({ encoding: oneOf("snorm16x4") }),
  opacityLogit: objectWith({ min: numberValue, max: numberValue }),
  colorDcScale: finiteVec3,
  colorShScale: fixedNumberArray(45),
  opacityShScale: fixedNumberArray(15),
  sourceColorShDegree: integerValue,
  sourceOpacityShDegree: integerValue,
});
const tileValidator = objectWith({
  pack: nonEmptyString,
  byteOffset: integerValue,
  byteLength: integerValue,
  recordCount: integerValue,
  sha256: nonEmptyString,
  quantization: quantizationValidator,
});
const nodeValidator = objectWith(
  {
    id: nonEmptyString,
    bounds: boundsValidator,
    gaussianCount: integerValue,
  },
  {
    children: childPair,
    tile: tileValidator,
  },
);
const packValidator = objectWith({
  id: nonEmptyString,
  path: nonEmptyString,
  byteLength: integerValue,
  recordCount: integerValue,
  sha256: nonEmptyString,
  payloadCrc32: nonEmptyString,
  byteOffset: integerValue,
});

const structuralDecoder = decoder<GsTileManifest>(
  "GSTile manifest",
  objectWith({
    schema: oneOf(GSTILE_SCHEMA),
    version: oneOf(GSTILE_VERSION),
    profile: oneOf(GSTILE_PROFILE),
    bundleId: nonEmptyString,
    source: objectWith({
      sha256: nonEmptyString,
      gaussianCount: integerValue,
      colorShDegree: integerValue,
      opacityShDegree: integerValue,
      recordBytes: integerValue,
    }),
    coordinateFrame: objectWith({
      kind: oneOf("local", "projected"),
      origin: finiteVec3,
      crs: nullable(nonEmptyString),
    }),
    root: nonEmptyString,
    nodes: arrayOf(nodeValidator),
    packs: arrayOf(packValidator),
    statistics: objectWith({
      leafCount: integerValue,
      packBytes: integerValue,
      bytesPerGaussian: numberValue,
      lod: nonEmptyString,
    }),
  }),
);

const safeRelativePath = (path: string) => {
  if (
    path.includes("\\") ||
    path.startsWith("/") ||
    path.split("/").some((part) => part === ".." || part === "")
  ) {
    throw new ResponseContractError(
      "GSTile manifest",
      "$.packs[].path",
      "safe relative POSIX path",
    );
  }
};

export const decodeGsTileManifest = (value: unknown): GsTileManifest => {
  const manifest = structuralDecoder(value);
  if (
    manifest.nodes.length === 0 ||
    manifest.packs.length === 0 ||
    !Number.isSafeInteger(manifest.source.gaussianCount) ||
    manifest.source.gaussianCount < 1
  ) {
    throw new ResponseContractError(
      "GSTile manifest",
      "$",
      "non-empty nodes and packs",
    );
  }
  const packIds = new Set<string>();
  const packsById = new Map<string, GsTilePack>();
  manifest.packs.forEach((pack) => {
    safeRelativePath(pack.path);
    if (
      packIds.has(pack.id) ||
      !Number.isSafeInteger(pack.byteLength) ||
      !Number.isSafeInteger(pack.recordCount) ||
      pack.recordCount < 1 ||
      pack.byteOffset !== GSTILE_PACK_HEADER_BYTES ||
      pack.byteLength !==
        GSTILE_PACK_HEADER_BYTES + pack.recordCount * GSTILE_RECORD_BYTES ||
      !/^[0-9a-f]{64}$/i.test(pack.sha256) ||
      !/^[0-9a-f]{8}$/i.test(pack.payloadCrc32)
    ) {
      throw new ResponseContractError(
        "GSTile manifest",
        "$.packs",
        "unique valid pack entries",
      );
    }
    packIds.add(pack.id);
    packsById.set(pack.id, pack);
  });
  const nodesById = new Map(manifest.nodes.map((node) => [node.id, node]));
  const nodeIds = new Set(nodesById.keys());
  if (nodeIds.size !== manifest.nodes.length || !nodeIds.has(manifest.root)) {
    throw new ResponseContractError(
      "GSTile manifest",
      "$.nodes",
      "unique nodes containing root",
    );
  }
  const rangesByPack = new Map<string, Array<{ start: number; end: number }>>();
  manifest.nodes.forEach((node) => {
    const hasChildren = node.children !== undefined;
    const hasTile = node.tile !== undefined;
    if (hasChildren === hasTile) {
      throw new ResponseContractError(
        "GSTile manifest",
        `$.nodes.${node.id}`,
        "exactly children or tile",
      );
    }
    node.bounds.min.forEach((minimum, index) => {
      if (minimum > node.bounds.max[index]) {
        throw new ResponseContractError(
          "GSTile manifest",
          `$.nodes.${node.id}.bounds`,
          "ordered finite bounds",
        );
      }
    });
    node.children?.forEach((child) => {
      if (!nodeIds.has(child)) {
        throw new ResponseContractError(
          "GSTile manifest",
          `$.nodes.${node.id}.children`,
          "known node ids",
        );
      }
    });
    if (node.tile) {
      const pack = packsById.get(node.tile.pack);
      const rangeEnd = node.tile.byteOffset + node.tile.byteLength;
      if (
        !pack ||
        !Number.isSafeInteger(node.tile.byteOffset) ||
        !Number.isSafeInteger(node.tile.byteLength) ||
        !Number.isSafeInteger(node.tile.recordCount) ||
        node.tile.recordCount < 1 ||
        node.tile.recordCount !== node.gaussianCount ||
        node.tile.byteLength !== node.tile.recordCount * GSTILE_RECORD_BYTES ||
        node.tile.byteOffset < GSTILE_PACK_HEADER_BYTES ||
        node.tile.byteOffset % GSTILE_RECORD_BYTES !== GSTILE_PACK_HEADER_BYTES ||
        !Number.isSafeInteger(rangeEnd) ||
        rangeEnd > (pack?.byteLength ?? 0) ||
        node.tile.sha256.toLowerCase() !== pack?.sha256.toLowerCase()
      ) {
        throw new ResponseContractError(
          "GSTile manifest",
          `$.nodes.${node.id}.tile`,
          "valid aligned range in a known pack",
        );
      }
      const ranges = rangesByPack.get(pack.id) ?? [];
      ranges.push({ start: node.tile.byteOffset, end: rangeEnd });
      rangesByPack.set(pack.id, ranges);
    }
  });

  for (const pack of manifest.packs) {
    const ranges = (rangesByPack.get(pack.id) ?? []).sort(
      (left, right) => left.start - right.start,
    );
    let cursor = GSTILE_PACK_HEADER_BYTES;
    for (const range of ranges) {
      if (range.start !== cursor) {
        throw new ResponseContractError(
          "GSTile manifest",
          `$.packs.${pack.id}`,
          "non-overlapping tile ranges covering the pack payload",
        );
      }
      cursor = range.end;
    }
    if (cursor !== pack.byteLength) {
      throw new ResponseContractError(
        "GSTile manifest",
        `$.packs.${pack.id}`,
        "tile ranges covering the complete pack payload",
      );
    }
  }

  const parentCount = new Map<string, number>();
  for (const node of manifest.nodes) {
    for (const child of node.children ?? []) {
      parentCount.set(child, (parentCount.get(child) ?? 0) + 1);
    }
  }
  for (const node of manifest.nodes) {
    const expectedParents = node.id === manifest.root ? 0 : 1;
    if ((parentCount.get(node.id) ?? 0) !== expectedParents) {
      throw new ResponseContractError(
        "GSTile manifest",
        `$.nodes.${node.id}`,
        "a rooted tree with exactly one parent per non-root node",
      );
    }
  }
  const visiting = new Set<string>();
  const visited = new Set<string>();
  const countSubtree = (nodeId: string): number => {
    if (visiting.has(nodeId)) {
      throw new ResponseContractError(
        "GSTile manifest",
        `$.nodes.${nodeId}`,
        "acyclic node tree",
      );
    }
    const node = nodesById.get(nodeId);
    if (!node) return 0;
    visiting.add(nodeId);
    const count = node.tile
      ? node.tile.recordCount
      : (node.children ?? []).reduce(
          (total, child) => total + countSubtree(child),
          0,
        );
    visiting.delete(nodeId);
    visited.add(nodeId);
    if (count !== node.gaussianCount) {
      throw new ResponseContractError(
        "GSTile manifest",
        `$.nodes.${nodeId}.gaussianCount`,
        "the sum of descendant tile records",
      );
    }
    return count;
  };
  const rootCount = countSubtree(manifest.root);
  if (
    visited.size !== manifest.nodes.length ||
    rootCount !== manifest.source.gaussianCount
  ) {
    throw new ResponseContractError(
      "GSTile manifest",
      "$",
      "one complete rooted tree matching source.gaussianCount",
    );
  }
  return manifest;
};

export const resolveGsTilePackUrl = (manifestUrl: string, path: string) => {
  safeRelativePath(path);
  return new URL(path, manifestUrl).toString();
};
