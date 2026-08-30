import { describe, expect, it } from "vitest";
import { NextRequest } from "next/server";

import { buildContentSecurityPolicy, proxy } from "./proxy";

describe("CSP proxy", () => {
  it("enforces a unique nonce and reports violations", () => {
    const first = proxy(new NextRequest("https://droneai.example.com/"));
    const second = proxy(new NextRequest("https://droneai.example.com/"));
    const firstPolicy = first.headers.get("content-security-policy") ?? "";
    const secondPolicy = second.headers.get("content-security-policy") ?? "";

    expect(firstPolicy).toContain("script-src 'self' 'nonce-");
    expect(firstPolicy).toContain("'strict-dynamic'");
    expect(firstPolicy).not.toContain("script-src 'self' 'unsafe-inline'");
    expect(firstPolicy).toContain("worker-src 'self' blob:");
    expect(firstPolicy).toContain("report-uri /api/csp-report");
    expect(first.headers.get("report-to")).toContain("/api/csp-report");
    expect(firstPolicy).not.toBe(secondPolicy);
  });

  it("allows unsafe-eval only for React development diagnostics", () => {
    expect(buildContentSecurityPolicy("nonce", true, false, false)).toContain("'unsafe-eval'");
    expect(buildContentSecurityPolicy("nonce", false, false, false)).not.toContain("'unsafe-eval'");
  });

  it("upgrades subresources only when the dashboard itself uses HTTPS", () => {
    const secure = proxy(new NextRequest("https://droneai.example.com/"));
    const loopback = proxy(new NextRequest("http://127.0.0.1:3000/"));
    const securePolicy = secure.headers.get("content-security-policy") ?? "";
    const loopbackPolicy = loopback.headers.get("content-security-policy") ?? "";

    expect(securePolicy).toContain("upgrade-insecure-requests");
    expect(securePolicy).not.toContain("http://127.0.0.1:*");
    expect(loopbackPolicy).not.toContain("upgrade-insecure-requests");
    expect(loopbackPolicy).toContain("img-src 'self' data: blob: https: http://localhost:*");
    expect(loopbackPolicy).toContain("http://127.0.0.1:*");
    expect(loopbackPolicy).toContain("ws://localhost:*");
    expect(loopbackPolicy).toContain("ws://127.0.0.1:*");
  });

  it("does not expose loopback services from a public HTTP hostname", () => {
    const response = proxy(new NextRequest("http://droneai.example.com/"));

    expect(response.headers.get("content-security-policy")).not.toContain(
      "http://127.0.0.1:*",
    );
  });
});
