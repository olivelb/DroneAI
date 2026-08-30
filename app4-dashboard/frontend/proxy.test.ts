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
    expect(buildContentSecurityPolicy("nonce", true)).toContain("'unsafe-eval'");
    expect(buildContentSecurityPolicy("nonce", false)).not.toContain("'unsafe-eval'");
  });
});
