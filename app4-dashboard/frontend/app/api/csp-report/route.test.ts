import { describe, expect, it } from "vitest";

import { POST } from "./route";

describe("CSP report endpoint", () => {
  it("accepts bounded browser reports without reflecting their contents", async () => {
    const response = await POST(new Request("https://droneai.example.com/api/csp-report", {
      method: "POST",
      headers: { "content-type": "application/reports+json" },
      body: JSON.stringify([{ type: "csp-violation", body: { blockedURL: "inline" } }]),
    }));

    expect(response.status).toBe(204);
    expect(await response.text()).toBe("");
  });

  it("rejects oversized reports", async () => {
    const response = await POST(new Request("https://droneai.example.com/api/csp-report", {
      method: "POST",
      headers: { "content-type": "application/csp-report" },
      body: "x".repeat(64 * 1024 + 1),
    }));

    expect(response.status).toBe(413);
  });
});
