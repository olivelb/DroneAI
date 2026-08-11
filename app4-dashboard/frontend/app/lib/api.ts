import type {
  FeatureBulkAction,
  GcpAuditEvent,
  GcpCollection,
  GcpBundle,
  GcpFeature,
  GcpImportOptions,
  GcpObservation,
  GcpSetSummary,
  MissionCatalogResponse,
  MissionDetail,
  RasterLayerStyle,
  RasterMetadata,
  RasterStyleRecipe,
} from "./types";

export type SessionPrincipal = {
  subject: string;
  role: "viewer" | "operator" | "admin";
  expires_in_seconds?: number;
};

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

const configuredApiBaseUrl = () => {
  if (typeof document === "undefined") return "";
  return (
    document
      .querySelector<HTMLMetaElement>('meta[name="droneai-api-url"]')
      ?.content.trim()
      .replace(/\/+$/, "") ?? ""
  );
};

const apiCredentials = (): RequestCredentials =>
  configuredApiBaseUrl() ? "include" : "same-origin";

export const getApiBaseUrl = () => {
  if (typeof window === "undefined") return "http://localhost:30080";
  return configuredApiBaseUrl() || `http://${window.location.hostname}:30080`;
};

export const getWsBaseUrl = () => {
  if (typeof window === "undefined") return "ws://localhost:30080";
  return getApiBaseUrl()
    .replace(/^http:/, "ws:")
    .replace(/^https:/, "wss:");
};

const api = async <T = unknown>(path: string, init?: RequestInit): Promise<T> => {
  const res = await fetch(`${getApiBaseUrl()}${path}`, {
    cache: "no-store",
    credentials: apiCredentials(),
    ...init,
  });
  const payload = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = payload && typeof payload === "object" && "detail" in payload
      ? String(payload.detail)
      : `HTTP ${res.status}`;
    if (res.status === 401 && typeof window !== "undefined") {
      window.dispatchEvent(new Event("droneai:unauthorized"));
    }
    throw new ApiError(res.status, detail);
  }
  return payload as T;
};

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
  return payload as SessionPrincipal;
};

export const fetchSession = () => api<SessionPrincipal>("/auth/session");

export const deleteSession = () =>
  api<{ status: string }>("/auth/session", { method: "DELETE" });

export const fetchSummary = () => api<{ missions?: Array<Record<string, unknown>>; active_vol_id?: string }>("/status/summary");
export const fetchMissionCatalog = (limit = 25, offset = 0) =>
  api<MissionCatalogResponse>(`/missions?${new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
  }).toString()}`);
export const fetchMissionDetail = (volId: string) =>
  api<MissionDetail>(`/missions/${encodeURIComponent(volId)}`);
export const fetchPods = () => api<{ pods?: Array<Record<string, unknown>>; error?: string }>("/pods");
export const fetchParameters = () => api<Record<string, unknown>>("/mission/parameters");
export const fetchBrowse = (prefix: string) => api<Array<Record<string, unknown>>>(`/browse?prefix=${encodeURIComponent(prefix)}`);

export const postMission = (params: Record<string, unknown>) =>
  api("/mission", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(params) });

export const postCancel = (volId: string) =>
  api(`/mission/cancel?vol_id=${encodeURIComponent(volId)}`, { method: "POST" });

export const deleteMission = (volId: string) =>
  api<{ status: string; message: string }>(`/mission/${encodeURIComponent(volId)}`, { method: "DELETE" });

export const deleteDataset = (name: string) =>
  api<{ status: string; message: string }>(`/datasets/${encodeURIComponent(name)}`, { method: "DELETE" });

export const postResume = (volId: string) =>
  api(`/mission/resume?vol_id=${encodeURIComponent(volId)}`, { method: "POST" });

type DirectUploadFile = {
  file_id: string;
  name: string;
  size: number;
  s3_key: string;
  total_parts: number;
  status: string;
};

type DirectUploadSession = {
  session_id: string;
  dataset: string;
  status: string;
  total: number;
  total_bytes: number;
  part_size: number;
  expires_at: string;
  files: DirectUploadFile[];
};

type UploadedPart = { part_number: number; etag: string };

const jsonRequest = (body?: unknown): RequestInit => ({
  method: "POST",
  headers: body === undefined ? undefined : { "Content-Type": "application/json" },
  body: body === undefined ? undefined : JSON.stringify(body),
});

const uploadPart = async (
  sessionId: string,
  descriptor: DirectUploadFile,
  partNumber: number,
  body: Blob,
): Promise<UploadedPart> => {
  let lastError: Error | null = null;
  for (let attempt = 1; attempt <= 3; attempt++) {
    try {
      const signed = await api<{ method: string; url: string }>(
        `/datasets/upload-sessions/${encodeURIComponent(sessionId)}`
          + `/files/${encodeURIComponent(descriptor.file_id)}/parts/${partNumber}`,
        jsonRequest(),
      );
      const response = await fetch(signed.url, {
        method: signed.method,
        body,
        credentials: "omit",
      });
      if (!response.ok) throw new Error(`S3 upload part failed: HTTP ${response.status}`);
      const etag = response.headers.get("ETag");
      if (!etag) {
        throw new Error("S3 did not expose the ETag response header; check bucket CORS");
      }
      return { part_number: partNumber, etag };
    } catch (error) {
      lastError = error instanceof Error ? error : new Error("Upload part failed");
    }
  }
  throw lastError ?? new Error("Upload part failed");
};

const uploadDirectFile = async (
  session: DirectUploadSession,
  descriptor: DirectUploadFile,
  file: File,
) => {
  const parts = new Array<UploadedPart>(descriptor.total_parts);
  let nextPart = 1;
  const worker = async () => {
    while (nextPart <= descriptor.total_parts) {
      const partNumber = nextPart++;
      const start = (partNumber - 1) * session.part_size;
      const body = file.slice(start, Math.min(start + session.part_size, file.size));
      parts[partNumber - 1] = await uploadPart(
        session.session_id,
        descriptor,
        partNumber,
        body,
      );
    }
  };
  await Promise.all(
    Array.from(
      { length: Math.min(4, descriptor.total_parts) },
      () => worker(),
    ),
  );
  await api(
    `/datasets/upload-sessions/${encodeURIComponent(session.session_id)}`
      + `/files/${encodeURIComponent(descriptor.file_id)}/complete`,
    jsonRequest({ parts }),
  );
};

export const uploadDataset = async (
  datasetName: string,
  files: FileList,
  onProgress?: (p: { total: number; completed: number; failed: number }) => void,
) => {
  const total = files.length;
  onProgress?.({ total, completed: 0, failed: 0 });
  let sessionId: string | null = null;
  try {
    const localFiles = Array.from(files);
    const session = await api<DirectUploadSession>(
      "/datasets/upload-sessions",
      jsonRequest({
        dataset_name: datasetName,
        files: localFiles.map((file) => ({
          name: file.name,
          size: file.size,
          content_type: file.type || "application/octet-stream",
        })),
      }),
    );
    sessionId = session.session_id;
    const filesByName = new Map(localFiles.map((file) => [file.name, file]));
    let completed = 0;
    let nextFile = 0;
    const worker = async () => {
      while (nextFile < session.files.length) {
        const descriptor = session.files[nextFile++];
        const file = filesByName.get(descriptor.name);
        if (!file || file.size !== descriptor.size) {
          throw new Error(`Local file changed after upload initialization: ${descriptor.name}`);
        }
        await uploadDirectFile(session, descriptor, file);
        completed += 1;
        onProgress?.({ total, completed, failed: 0 });
      }
    }
    await Promise.all(
      Array.from({ length: Math.min(3, session.files.length) }, () => worker()),
    );
    const result = await api<{
      total: number;
      completed: number;
      failed: number;
      status: string;
    }>(
      `/datasets/upload-sessions/${encodeURIComponent(session.session_id)}/complete`,
      jsonRequest(),
    );
    onProgress?.({ total, completed: result.completed, failed: result.failed });
    return result;
  } catch (error) {
    if (sessionId) {
      await api(`/datasets/upload-sessions/${encodeURIComponent(sessionId)}`, {
        method: "DELETE",
      }).catch(() => undefined);
    }
    const message = error instanceof Error ? error.message : "network error";
    const results = Array.from(files, (file) => ({
      name: file.name,
      status: "error",
      error: message,
    }));
    onProgress?.({ total, completed: 0, failed: total });
    return {
      total,
      completed: 0,
      failed: total,
      status: "partial",
      files: results,
    };
  }
};

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
) => api<RasterMetadata>(`/maps/${encodeURIComponent(missionId)}/metadata/${layer}`);

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
) => api<import("geojson").FeatureCollection>(
  `/maps/${encodeURIComponent(missionId)}/vectors.geojson?${new URLSearchParams({
    bbox: bbox.join(","),
    sources: (options?.sources ?? ["legacy", "manual"]).join(","),
    ...(options?.runIds?.length
      ? { run_ids: options.runIds.join(",") }
      : {}),
  }).toString()}`,
);

export const fetchAnalyses = (missionId: string) =>
  api<{ runs: import("./types").AnalysisRun[] }>(
    `/maps/${encodeURIComponent(missionId)}/analyses`,
  );

export const createAnalysis = (
  missionId: string,
  request: import("./types").AnalysisCreate,
) => api<import("./types").AnalysisRun>(
  `/maps/${encodeURIComponent(missionId)}/analyses`,
  {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  },
);

export const retryAnalysis = (missionId: string, runId: string) =>
  api<import("./types").AnalysisRun>(
    `/maps/${encodeURIComponent(missionId)}/analyses/${encodeURIComponent(runId)}/retry`,
    { method: "POST" },
  );

export const cancelAnalysis = (missionId: string, runId: string) =>
  api<import("./types").AnalysisRun>(
    `/maps/${encodeURIComponent(missionId)}/analyses/${encodeURIComponent(runId)}/cancel`,
    { method: "POST" },
  );

export const getAnalysisVectors = (
  missionId: string,
  runId: string,
  bbox: [number, number, number, number],
) => api<import("geojson").FeatureCollection>(
  `/maps/${encodeURIComponent(missionId)}/analyses/${encodeURIComponent(runId)}/vectors.geojson?bbox=${bbox.join(",")}`,
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
  return api<import("geojson").FeatureCollection & { bounds?: [number, number, number, number] | null }>(
    `/maps/${encodeURIComponent(missionId)}/search?${params.toString()}`,
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
) => api<import("geojson").Feature>(
  `/maps/${encodeURIComponent(missionId)}/features`,
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
) => api<import("geojson").Feature>(
  `/maps/${encodeURIComponent(missionId)}/features/${encodeURIComponent(featureId)}`,
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
  api<void>(
    `/maps/${encodeURIComponent(missionId)}/features/${encodeURIComponent(featureId)}?${new URLSearchParams({ reason }).toString()}`,
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
) => api<{
  action: FeatureBulkAction;
  requested_count: number;
  changed_count: number;
  features: import("geojson").Feature[];
}>(`/maps/${encodeURIComponent(missionId)}/features/bulk`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(request),
});

export const fetchRasterStyles = (
  missionId: string,
  layer: "ortho" | "depth",
) => api<{ layer: string; styles: RasterLayerStyle[] }>(
  `/maps/${encodeURIComponent(missionId)}/styles/${layer}`,
);

export const createRasterStyle = (
  missionId: string,
  layer: "ortho" | "depth",
  request: { name: string; style: RasterStyleRecipe; is_default?: boolean },
) => api<RasterLayerStyle>(
  `/maps/${encodeURIComponent(missionId)}/styles/${layer}`,
  {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  },
);

export const fetchGroundControl = (missionId: string) =>
  api<GcpCollection>(`/maps/${encodeURIComponent(missionId)}/gcps`);

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
  return api<{ gcp_set: GcpSetSummary; candidate_generation: Record<string, unknown> }>(
    `/maps/${encodeURIComponent(missionId)}/gcps/import`,
    { method: "POST", body },
  );
};

export const updateGroundControlPoint = (
  missionId: string,
  pointId: string,
  request: Record<string, unknown>,
) => api<GcpFeature>(
  `/maps/${encodeURIComponent(missionId)}/gcps/points/${encodeURIComponent(pointId)}`,
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
) => api<GcpObservation>(
  `/maps/${encodeURIComponent(missionId)}/gcps/observations/${encodeURIComponent(observationId)}`,
  {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  },
);

export const prepareGroundControlBundle = (missionId: string, setId: string) =>
  api<GcpBundle>(
    `/maps/${encodeURIComponent(missionId)}/gcps/${encodeURIComponent(setId)}/bundle`,
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
  return api<{
    gcp_set: GcpCollection;
    candidate_generation: { added_observation_count: number };
  }>(
    `/maps/${encodeURIComponent(missionId)}/gcps/${encodeURIComponent(setId)}/candidates/refresh?${query}`,
    { method: "POST" },
  );
};

export const fetchGroundControlAudit = (missionId: string, setId: string) =>
  api<{ set_id: string; events: GcpAuditEvent[] }>(
    `/maps/${encodeURIComponent(missionId)}/gcps/${encodeURIComponent(setId)}/audit`,
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
