import { NextRequest, NextResponse } from "next/server";

export const buildContentSecurityPolicy = (
  nonce: string,
  isDevelopment: boolean,
  upgradeInsecureRequests: boolean,
  allowLoopbackConnections: boolean,
  allowedOrigins?: string[],
) => [
  "default-src 'self'",
  `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${isDevelopment ? " 'unsafe-eval'" : ""}`,
  "style-src 'self' 'unsafe-inline'",
  `img-src 'self' data: blob: ${allowedOrigins?.join(" ") ?? "https:"}${allowLoopbackConnections
    ? " http://localhost:* http://127.0.0.1:*"
    : ""}`,
  "font-src 'self' data:",
  "worker-src 'self' blob:",
  `connect-src 'self' ${allowedOrigins?.join(" ") ?? "https: wss:"}${allowLoopbackConnections
    ? " http://localhost:* http://127.0.0.1:* ws://localhost:* ws://127.0.0.1:*"
    : ""}`,
  "manifest-src 'self'",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
  "report-uri /api/csp-report",
  "report-to csp-endpoint",
  ...(upgradeInsecureRequests ? ["upgrade-insecure-requests"] : []),
].join("; ");

export function proxy(request: NextRequest) {
  const nonce = Buffer.from(crypto.randomUUID()).toString("base64");
  const policy = buildContentSecurityPolicy(
    nonce,
    process.env.NODE_ENV === "development",
    request.nextUrl.protocol === "https:",
    ["localhost", "127.0.0.1", "[::1]"].includes(request.nextUrl.hostname),
  );
  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-nonce", nonce);
  requestHeaders.set("Content-Security-Policy", policy);

  const response = NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set("Content-Security-Policy", policy);
  const origins = new Set<string>();
  for (const value of [process.env.DRONEAI_PUBLIC_API_URL, ...(process.env.DRONEAI_CSP_ORIGINS ?? "").split(",")]) {
    if (!value?.trim()) continue;
    const url = new URL(value.trim());
    if (!["https:", "wss:"].includes(url.protocol)) continue;
    origins.add(url.origin);
    if (url.protocol === "https:") origins.add(url.origin.replace(/^https:/, "wss:"));
  }
  response.headers.set("Content-Security-Policy-Report-Only", buildContentSecurityPolicy(
    nonce, process.env.NODE_ENV === "development", request.nextUrl.protocol === "https:",
    false, [...origins],
  ));
  response.headers.set(
    "Report-To",
    JSON.stringify({
      group: "csp-endpoint",
      max_age: 10886400,
      endpoints: [{ url: new URL("/api/csp-report", request.url).toString() }],
    }),
  );
  return response;
}

export const config = {
  matcher: [
    {
      source: "/((?!_next/static|_next/image).*)",
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
  ],
};
