import type {
  FeatureBulkAction,
  GcpImportOptions,
  RasterStyleRecipe,
} from "./types";
import {
  api,
  apiCredentials,
  ApiError,
  getApiBaseUrl,
} from "./api-client";
import { parseSessionPrincipal } from "./api-contracts";
import { parseNoContent } from "./contract-decoder";
import {
  parseGcpAudit,
  parseGcpBundle,
  parseGcpCandidateRefresh,
  parseGcpCollection,
  parseGcpFeature,
  parseGcpImport,
  parseGcpObservation,
} from "./gcp-api-contracts";
import {
  parseAnalysisList,
  parseAnalysisRun,
  parseBulkFeatureResponse,
  parseFeature,
  parseFeatureCollection,
  parseRasterMetadata,
  parseRasterStyle,
  parseRasterStyleList,
  parseSearchFeatureCollection,
} from "./map-api-contracts";
import {
  parseCommandResponse,
  parseDatasetItems,
  parseMissionCatalog,
  parseMissionDetail,
  parseMissionSummaryResponse,
  parseParameterConfig,
} from "./mission-api-contracts";

export {
  ApiError,
  getApiBaseUrl,
  getWsBaseUrl,
} from "./api-client";
export { uploadDataset } from "./api-upload";
export type { SessionPrincipal } from "./types";

export const createSession = async (apiKey: string) => {
  const res = await fetch(`${getApiBaseUrl()}/auth/session`, {
    method: "POST",
    cache: "no-store",
    credentials: apiCredentials(),
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ api_key: apiKey }),
  });
  const payload = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = payload && typeof payload === "object" && "detail" in payload
      ? String(payload.detail)
      : `HTTP ${res.status}`;
    throw new ApiError(res.status, detail);
  }
  return parseSessionPrincipal(payload);
};

export const fetchSession = async () =>
  api("/auth/session", parseSessionPrincipal);

export const deleteSession = () =>
  api("/auth/session", parseCommandResponse, { method: "DELETE" });

export const fetchSummary = () =>
  api("/status/summary", parseMissionSummaryResponse);
export const fetchMissionCatalog = (limit = 25, offset = 0) =>
  api(`/missions?${new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
  }).toString()}`, parseMissionCatalog);
export const fetchMissionDetail = (volId: string) =>
  api(`/missions/${encodeURIComponent(volId)}`, parseMissionDetail);
export const fetchParameters = () =>
  api("/mission/parameters", parseParameterConfig);
export const fetchBrowse = (prefix: string) =>
  api(`/browse?prefix=${encodeURIComponent(prefix)}`, parseDatasetItems);

export const postMission = (params: Record<string, unknown>) =>
  api("/mission", parseCommandResponse, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(params) });

export const postCancel = (volId: string) =>
  api(`/mission/cancel?vol_id=${encodeURIComponent(volId)}`, parseCommandResponse, { method: "POST" });

export const deleteMission = (volId: string) =>
  api(`/mission/${encodeURIComponent(volId)}`, parseCommandResponse, { method: "DELETE" });

export const deleteDataset = (name: string) =>
  api(`/datasets/${encodeURIComponent(name)}`, parseCommandResponse, { method: "DELETE" });

export const postResume = (volId: string) =>
  api(`/mission/resume?vol_id=${encodeURIComponent(volId)}`, parseCommandResponse, { method: "POST" });

const encodeS3Key = (s3Key: string) => s3Key.split("/").map(encodeURIComponent).join("/");

export const getFileUrl = (s3Key: string) => `${getApiBaseUrl()}/files/${encodeS3Key(s3Key)}`;
export const getPreviewUrl = (s3Key: string, maxSize = 4096, colormap = "") => {
  const params = new URLSearchParams({ max_size: String(maxSize) });
  if (colormap) params.set("colormap", colormap);
  return `${getApiBaseUrl()}/preview/${encodeS3Key(s3Key)}?${params.toString()}`;
};

export const getMapMetadata = (
  missionId: string,
  layer: "ortho" | "depth",
) => api(
  `/maps/${encodeURIComponent(missionId)}/metadata/${layer}`,
  parseRasterMetadata,
);

export const getMapTileUrl = (
  missionId: string,
  layer: "ortho" | "depth",
  style?: RasterStyleRecipe,
) => {
  const url = `${getApiBaseUrl()}/maps/${encodeURIComponent(missionId)}/tiles/${layer}/{z}/{x}/{y}.png`;
  if (!style) return url;
  const params = new URLSearchParams({
    bands: style.bands.join(","),
    palette: style.palette,
  });
  if (style.display_ranges.length) {
    params.set(
      "display_ranges",
      style.display_ranges
        .map((range) => (range ? `${range[0]}:${range[1]}` : "auto"))
        .join(","),
    );
  }
  return `${url}?${params.toString()}`;
};

export const getVectorLayer = (
  missionId: string,
  bbox: [number, number, number, number],
  options?: { sources?: string[]; runIds?: string[] },
) => api(
  `/maps/${encodeURIComponent(missionId)}/vectors.geojson?${new URLSearchParams({
    bbox: bbox.join(","),
    sources: (options?.sources ?? ["legacy", "manual"]).join(","),
    ...(options?.runIds?.length
      ? { run_ids: options.runIds.join(",") }
      : {}),
  }).toString()}`,
  parseFeatureCollection,
);

export const fetchAnalyses = (missionId: string) =>
  api(
    `/maps/${encodeURIComponent(missionId)}/analyses`,
    parseAnalysisList,
  );

export const createAnalysis = (
  missionId: string,
  request: import("./types").AnalysisCreate,
) => api(
  `/maps/${encodeURIComponent(missionId)}/analyses`,
  parseAnalysisRun,
  {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  },
);

export const retryAnalysis = (missionId: string, runId: string) =>
  api(
    `/maps/${encodeURIComponent(missionId)}/analyses/${encodeURIComponent(runId)}/retry`,
    parseAnalysisRun,
    { method: "POST" },
  );

export const cancelAnalysis = (missionId: string, runId: string) =>
  api(
    `/maps/${encodeURIComponent(missionId)}/analyses/${encodeURIComponent(runId)}/cancel`,
    parseAnalysisRun,
    { method: "POST" },
  );

export const getAnalysisVectors = (
  missionId: string,
  runId: string,
  bbox: [number, number, number, number],
) => api(
  `/maps/${encodeURIComponent(missionId)}/analyses/${encodeURIComponent(runId)}/vectors.geojson?bbox=${bbox.join(",")}`,
  parseFeatureCollection,
);

export const searchMapFeatures = (
  missionId: string,
  filters: {
    q?: string;
    source?: string;
    runId?: string;
    className?: string;
    minConfidence?: number;
    reviewed?: boolean;
    deleted?: boolean;
  },
) => {
  const params = new URLSearchParams();
  if (filters.q) params.set("q", filters.q);
  if (filters.source) params.set("source", filters.source);
  if (filters.runId) params.set("run_id", filters.runId);
  if (filters.className) params.set("class_name", filters.className);
  if (filters.minConfidence !== undefined) {
    params.set("min_confidence", String(filters.minConfidence));
  }
  if (filters.reviewed !== undefined) {
    params.set("reviewed", String(filters.reviewed));
  }
  if (filters.deleted !== undefined) {
    params.set("deleted", String(filters.deleted));
  }
  return api(
    `/maps/${encodeURIComponent(missionId)}/search?${params.toString()}`,
    parseSearchFeatureCollection,
  );
};

export const createMapFeature = (
  missionId: string,
  request: {
    geometry: import("geojson").Geometry;
    name: string;
    description: string;
    color: string;
    tags: string[];
    properties?: Record<string, unknown>;
  },
) => api(
  `/maps/${encodeURIComponent(missionId)}/features`,
  parseFeature,
  {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  },
);

export const updateMapFeature = (
  missionId: string,
  featureId: string,
  request: Record<string, unknown>,
) => api(
  `/maps/${encodeURIComponent(missionId)}/features/${encodeURIComponent(featureId)}`,
  parseFeature,
  {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  },
);

export const deleteMapFeature = (
  missionId: string,
  featureId: string,
  reason = "",
) =>
  api(
    `/maps/${encodeURIComponent(missionId)}/features/${encodeURIComponent(featureId)}?${new URLSearchParams({ reason }).toString()}`,
    parseNoContent,
    { method: "DELETE" },
  );

export const mutateMapFeaturesBulk = (
  missionId: string,
  request: {
    action: FeatureBulkAction;
    feature_ids: string[];
    expected_versions?: Record<string, number>;
    reason?: string;
  },
) => api(`/maps/${encodeURIComponent(missionId)}/features/bulk`, parseBulkFeatureResponse, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(request),
});

export const fetchRasterStyles = (
  missionId: string,
  layer: "ortho" | "depth",
) => api(
  `/maps/${encodeURIComponent(missionId)}/styles/${layer}`,
  parseRasterStyleList,
);

export const createRasterStyle = (
  missionId: string,
  layer: "ortho" | "depth",
  request: { name: string; style: RasterStyleRecipe; is_default?: boolean },
) => api(
  `/maps/${encodeURIComponent(missionId)}/styles/${layer}`,
  parseRasterStyle,
  {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  },
);

export const fetchGroundControl = (missionId: string) =>
  api(`/maps/${encodeURIComponent(missionId)}/gcps`, parseGcpCollection);

export const importGroundControl = (
  missionId: string,
  file: File,
  options: GcpImportOptions,
) => {
  const body = new FormData();
  body.set("upload", file, file.name);
  body.set("name", options.name);
  if (options.sourceCrs) body.set("source_crs", options.sourceCrs);
  body.set("default_role", options.defaultRole);
  body.set("horizontal_accuracy_m", String(options.horizontalAccuracyM));
  body.set("vertical_accuracy_m", String(options.verticalAccuracyM));
  body.set("image_accuracy_px", String(options.imageAccuracyPx));
  body.set("candidate_radius_m", String(options.candidateRadiusM));
  body.set("max_candidates", String(options.maxCandidates));
  body.set("column_profile", options.columnProfile === "custom" ? "auto" : options.columnProfile);
  if (options.columnMapping && Object.values(options.columnMapping).some(Boolean)) {
    body.set("column_mapping", JSON.stringify(options.columnMapping));
  }
  return api(
    `/maps/${encodeURIComponent(missionId)}/gcps/import`,
    parseGcpImport,
    { method: "POST", body },
  );
};

export const updateGroundControlPoint = (
  missionId: string,
  pointId: string,
  request: Record<string, unknown>,
) => api(
  `/maps/${encodeURIComponent(missionId)}/gcps/points/${encodeURIComponent(pointId)}`,
  parseGcpFeature,
  {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  },
);

export const updateGroundControlObservation = (
  missionId: string,
  observationId: string,
  request: {
    status: "candidate" | "marked" | "skipped";
    pixel_x?: number;
    pixel_y?: number;
    version: number;
  },
) => api(
  `/maps/${encodeURIComponent(missionId)}/gcps/observations/${encodeURIComponent(observationId)}`,
  parseGcpObservation,
  {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  },
);

export const prepareGroundControlBundle = (missionId: string, setId: string) =>
  api(
    `/maps/${encodeURIComponent(missionId)}/gcps/${encodeURIComponent(setId)}/bundle`,
    parseGcpBundle,
    { method: "POST" },
  );

export const refreshGroundControlCandidates = (
  missionId: string,
  setId: string,
  candidateRadiusM = 250,
  maxCandidates = 20,
) => {
  const query = new URLSearchParams({
    candidate_radius_m: String(candidateRadiusM),
    max_candidates: String(maxCandidates),
  });
  return api(
    `/maps/${encodeURIComponent(missionId)}/gcps/${encodeURIComponent(setId)}/candidates/refresh?${query}`,
    parseGcpCandidateRefresh,
    { method: "POST" },
  );
};

export const fetchGroundControlAudit = (missionId: string, setId: string) =>
  api(
    `/maps/${encodeURIComponent(missionId)}/gcps/${encodeURIComponent(setId)}/audit`,
    parseGcpAudit,
  );

type SaveFilePickerType = {
  description: string;
  accept: Record<string, string[]>;
};

type SaveFileHandle = {
  createWritable: () => Promise<WritableStream<Uint8Array>>;
};

type SaveFilePickerWindow = Window & {
  showSaveFilePicker?: (options: {
    suggestedName: string;
    types: SaveFilePickerType[];
  }) => Promise<SaveFileHandle>;
};

export type ExportDownloadResult = "saved" | "download" | "cancelled";

export const downloadMapExport = async (
  path: string,
  suggestedName: string,
  fileType: SaveFilePickerType,
): Promise<ExportDownloadResult> => {
  const url = `${getApiBaseUrl()}${path}`;
  const picker = (window as SaveFilePickerWindow).showSaveFilePicker?.bind(
    window,
  );
  if (!picker) {
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = suggestedName;
    anchor.rel = "noopener";
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    return "download";
  }

  let handle: SaveFileHandle;
  try {
    handle = await picker({
      suggestedName,
      types: [fileType],
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      return "cancelled";
    }
    throw error;
  }

  const response = await fetch(url, {
    cache: "no-store",
    credentials: apiCredentials(),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    const detail =
      payload && typeof payload === "object" && "detail" in payload
        ? String(payload.detail)
        : `HTTP ${response.status}`;
    throw new ApiError(response.status, detail);
  }
  if (!response.body) throw new Error("Le navigateur ne peut pas enregistrer ce flux.");

  const writable = await handle.createWritable();
  await response.body.pipeTo(writable);
  return "saved";
};

export const getRasterExportPath = (
  missionId: string,
  layer: "ortho" | "depth",
  format: "cog" | "geotiff",
) =>
  `/maps/${encodeURIComponent(missionId)}/export/raster/${layer}?format=${format}`;

export const getVectorExportPath = (
  missionId: string,
  format: "gpkg" | "geojson",
  scope: "all" | "manual" | "ai" | "legacy",
  runIds: string[] = [],
  crs = "raster",
) => {
  const params = new URLSearchParams({ format, scope, crs });
  if (runIds.length) params.set("run_ids", runIds.join(","));
  return `/maps/${encodeURIComponent(missionId)}/export/vectors?${params.toString()}`;
};
