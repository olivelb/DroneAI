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

export const uploadDataset = async (
  datasetName: string,
  files: FileList,
  onProgress?: (p: { total: number; completed: number; failed: number }) => void,
) => {
  const total = files.length;
  let completed = 0;
  let failed = 0;
  const results: Array<{ name: string; status: string; s3_key?: string; error?: string }> = [];

  for (let i = 0; i < files.length; i++) {
    const formData = new FormData();
    formData.append("file", files[i]);
    try {
      const res = await fetch(
        `${getApiBaseUrl()}/datasets/upload-file?dataset_name=${encodeURIComponent(datasetName)}`,
        { method: "POST", body: formData, credentials: apiCredentials() },
      );
      const r = await res.json();
      if (r.status === "ok") {
        completed++;
      } else {
        failed++;
      }
      results.push(r);
    } catch {
      failed++;
      results.push({ name: files[i].name, status: "error", error: "network error" });
    }
    onProgress?.({ total, completed, failed });
  }

  return { total, completed, failed, status: failed === 0 ? "done" : "partial", files: results };
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
) => api(`/maps/${encodeURIComponent(missionId)}/metadata/${layer}`);

export const getMapTileUrl = (
  missionId: string,
  layer: "ortho" | "depth",
) => `${getApiBaseUrl()}/maps/${encodeURIComponent(missionId)}/tiles/${layer}/{z}/{x}/{y}.png`;

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

export const deleteMapFeature = (missionId: string, featureId: string) =>
  api<void>(
    `/maps/${encodeURIComponent(missionId)}/features/${encodeURIComponent(featureId)}`,
    { method: "DELETE" },
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
