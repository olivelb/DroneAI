import type { Decoder } from "./contract-decoder";

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string, public retryAfterMs = 0) {
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
  const configured = configuredApiBaseUrl();
  if (configured) {
    const parsed = new URL(configured);
    if (!["http:", "https:"].includes(parsed.protocol) || parsed.username || parsed.password) {
      throw new Error("Invalid DroneAI API URL configuration");
    }
    if (typeof window !== "undefined" && window.location.protocol === "https:" && parsed.protocol !== "https:") {
      throw new Error("DroneAI requires an HTTPS API URL on this page");
    }
    return configured;
  }
  if (process.env.NODE_ENV === "production") {
    throw new Error("DroneAI API URL is missing; configure DRONEAI_PUBLIC_API_URL");
  }
  return `http://${typeof window === "undefined" ? "localhost" : window.location.hostname}:30080`;
};

export const getWsBaseUrl = () => {
  return getApiBaseUrl()
    .replace(/^http:/, "ws:")
    .replace(/^https:/, "wss:");
};

export const api = async <T>(
  path: string,
  decode: Decoder<T>,
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
    const retryAfter = response.headers.get("Retry-After");
    const retryAfterMs = retryAfter
      ? (/^\d+$/.test(retryAfter) ? Number(retryAfter) * 1000 : Math.max(0, Date.parse(retryAfter) - Date.now()))
      : 0;
    throw new ApiError(response.status, detail, retryAfterMs);
  }
  return decode(payload);
};
