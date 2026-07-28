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
