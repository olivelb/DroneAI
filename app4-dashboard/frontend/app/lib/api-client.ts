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

export const apiCredentials = (): RequestCredentials =>
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

export const api = async <T = unknown>(
  path: string,
  init?: RequestInit,
): Promise<T> => {
  const response = await fetch(`${getApiBaseUrl()}${path}`, {
    cache: "no-store",
    credentials: apiCredentials(),
    ...init,
  });
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = payload && typeof payload === "object" && "detail" in payload
      ? String(payload.detail)
      : `HTTP ${response.status}`;
    if (response.status === 401 && typeof window !== "undefined") {
      window.dispatchEvent(new Event("droneai:unauthorized"));
    }
    throw new ApiError(response.status, detail);
  }
  return payload as T;
};
