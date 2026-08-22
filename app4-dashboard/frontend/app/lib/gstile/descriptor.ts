import { ResponseContractError } from "../contract-decoder";
import { decodeGsTileManifest, type GsTileManifest } from "./contracts";

export type GsTileViewerDescriptor = {
  artifactId: string;
  bundleId: string;
  expiresAt: string;
  manifest: GsTileManifest;
  packUrls: ReadonlyMap<string, string>;
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
  return { artifactId, bundleId, expiresAt, manifest, packUrls };
};
