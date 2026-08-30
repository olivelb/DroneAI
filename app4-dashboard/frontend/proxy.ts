import { NextRequest, NextResponse } from "next/server";

export const buildContentSecurityPolicy = (
  nonce: string,
  isDevelopment: boolean,
  upgradeInsecureRequests: boolean,
  allowLoopbackConnections: boolean,
) => [
  "default-src 'self'",
  `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${isDevelopment ? " 'unsafe-eval'" : ""}`,
  "style-src 'self' 'unsafe-inline'",
  `img-src 'self' data: blob: https:${allowLoopbackConnections
    ? " http://localhost:* http://127.0.0.1:*"
    : ""}`,
  "font-src 'self' data:",
  "worker-src 'self' blob:",
  `connect-src 'self' https: wss:${allowLoopbackConnections
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
