import { ResponseContractError } from "../contract-decoder";
import type { GaussianViewFrame } from "./backend";
import { decodeGsTileManifest, type GsTileManifest } from "./contracts";

export type GsTileViewerDescriptor = {
  artifactId: string;
  bundleId: string;
  expiresAt: string;
  manifest: GsTileManifest;
  packUrls: ReadonlyMap<string, string>;
  recommendedView: GaussianViewFrame | null;
};

const objectValue = (value: unknown, path: string) => {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new ResponseContractError("Gaussian viewer descriptor", path, "object");
  }
  return value as Record<string, unknown>;
};

const stringValue = (value: unknown, path: string) => {
  if (typeof value !== "string" || !value) {
    throw new ResponseContractError(
      "Gaussian viewer descriptor",
      path,
      "non-empty string",
    );
  }
  return value;
};

const vectorValue = (
  value: unknown,
  path: string,
): [number, number, number] => {
  if (
    !Array.isArray(value) ||
    value.length !== 3 ||
    value.some((entry) => typeof entry !== "number" || !Number.isFinite(entry))
  ) {
    throw new ResponseContractError(
      "Gaussian viewer descriptor",
      path,
      "finite 3-item vector",
    );
  }
  return [value[0], value[1], value[2]];
};

const dot = (
  left: readonly number[],
  right: readonly number[],
) => left[0] * right[0] + left[1] * right[1] + left[2] * right[2];

const decodeRecommendedView = (value: unknown): GaussianViewFrame => {
  const payload = objectValue(value, "$.recommendedView");
  if (payload.kind !== "facade") {
    throw new ResponseContractError(
      "Gaussian viewer descriptor",
      "$.recommendedView.kind",
      "facade",
    );
  }
  const right = vectorValue(payload.right, "$.recommendedView.right");
  const up = vectorValue(payload.up, "$.recommendedView.up");
  const outward = vectorValue(payload.outward, "$.recommendedView.outward");
  const cross: [number, number, number] = [
    right[1] * up[2] - right[2] * up[1],
    right[2] * up[0] - right[0] * up[2],
    right[0] * up[1] - right[1] * up[0],
  ];
  const tolerance = 1e-3;
  const isUnit = (vector: readonly number[]) =>
    Math.abs(dot(vector, vector) - 1) <= tolerance;
  if (
    !isUnit(right) ||
    !isUnit(up) ||
    !isUnit(outward) ||
    Math.abs(dot(right, up)) > tolerance ||
    Math.abs(dot(right, outward)) > tolerance ||
    Math.abs(dot(up, outward)) > tolerance ||
    dot(cross, outward) < 1 - tolerance
  ) {
    throw new ResponseContractError(
      "Gaussian viewer descriptor",
      "$.recommendedView",
      "right-handed orthonormal frame",
    );
  }
  return { kind: "facade", right, up, outward };
};

export const decodeGsTileViewerDescriptor = (
  value: unknown,
): GsTileViewerDescriptor => {
  const payload = objectValue(value, "$");
  if (
    payload.schema !== "droneai-gaussian-viewer-descriptor" ||
    payload.version !== 1
  ) {
    throw new ResponseContractError(
      "Gaussian viewer descriptor",
      "$",
      "supported schema version",
    );
  }
  const artifactId = stringValue(payload.artifactId, "$.artifactId");
  const bundleId = stringValue(payload.bundleId, "$.bundleId");
  const expiresAt = stringValue(payload.expiresAt, "$.expiresAt");
  if (!Number.isFinite(Date.parse(expiresAt))) {
    throw new ResponseContractError(
      "Gaussian viewer descriptor",
      "$.expiresAt",
      "ISO timestamp",
    );
  }
  const manifest = decodeGsTileManifest(payload.manifest);
  if (bundleId !== manifest.bundleId || !Array.isArray(payload.packs)) {
    throw new ResponseContractError(
      "Gaussian viewer descriptor",
      "$",
      "matching bundle and pack list",
    );
  }
  const manifestPacks = new Map(manifest.packs.map((pack) => [pack.id, pack]));
  const packUrls = new Map<string, string>();
  payload.packs.forEach((rawPack, index) => {
    const pack = objectValue(rawPack, `$.packs[${index}]`);
    const id = stringValue(pack.id, `$.packs[${index}].id`);
    const url = stringValue(pack.url, `$.packs[${index}].url`);
    const expected = manifestPacks.get(id);
    if (
      !expected ||
      packUrls.has(id) ||
      pack.byteLength !== expected.byteLength ||
      pack.sha256 !== expected.sha256
    ) {
      throw new ResponseContractError(
        "Gaussian viewer descriptor",
        `$.packs[${index}]`,
        "unique manifest-matching pack",
      );
    }
    let parsed: URL;
    try {
      parsed = new URL(url);
    } catch {
      throw new ResponseContractError(
        "Gaussian viewer descriptor",
        `$.packs[${index}].url`,
        "absolute URL",
      );
    }
    if (!(["http:", "https:"] as string[]).includes(parsed.protocol)) {
      throw new ResponseContractError(
        "Gaussian viewer descriptor",
        `$.packs[${index}].url`,
        "HTTP(S) URL",
      );
    }
    packUrls.set(id, parsed.toString());
  });
  if (packUrls.size !== manifest.packs.length) {
    throw new ResponseContractError(
      "Gaussian viewer descriptor",
      "$.packs",
      "one URL per manifest pack",
    );
  }
  const recommendedView =
    payload.recommendedView === undefined
      ? null
      : decodeRecommendedView(payload.recommendedView);
  return { artifactId, bundleId, expiresAt, manifest, packUrls, recommendedView };
};
