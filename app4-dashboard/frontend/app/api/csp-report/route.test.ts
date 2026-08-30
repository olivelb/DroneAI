import { afterEach, describe, expect, it, vi } from "vitest";

import { POST } from "./route";

describe("CSP report endpoint", () => {
  afterEach(() => vi.restoreAllMocks());

  it("accepts bounded browser reports without reflecting their contents", async () => {
    const warning = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const response = await POST(new Request("https://droneai.example.com/api/csp-report", {
      method: "POST",
      headers: { "content-type": "application/reports+json" },
      body: JSON.stringify([{ type: "csp-violation", body: {
        documentURL: "https://droneai.example.com/mission?token=document-secret",
        blockedURL: "https://cdn.example.com/script.js?token=blocked-secret",
        effectiveDirective: "script-src-elem",
      } }]),
    }));

    expect(response.status).toBe(204);
    expect(await response.text()).toBe("");
    expect(warning).toHaveBeenCalledOnce();
    const logged = warning.mock.calls[0][0];
    expect(logged).toContain("droneai_csp_violation");
    expect(logged).toContain("https://cdn.example.com/script.js");
    expect(logged).not.toContain("document-secret");
    expect(logged).not.toContain("blocked-secret");
  });

  it("rejects oversized reports", async () => {
    const response = await POST(new Request("https://droneai.example.com/api/csp-report", {
      method: "POST",
      headers: { "content-type": "application/csp-report" },
      body: "x".repeat(64 * 1024 + 1),
    }));

    expect(response.status).toBe(413);
  });

  it("rejects malformed reports without logging their raw body", async () => {
    const warning = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const response = await POST(new Request("https://droneai.example.com/api/csp-report", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: "secret-not-json",
    }));

    expect(response.status).toBe(400);
    expect(warning).not.toHaveBeenCalled();
  });
});
