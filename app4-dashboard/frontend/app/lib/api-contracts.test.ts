import { describe, expect, it } from "vitest";

import { parseSessionPrincipal } from "./api-contracts";

describe("authentication API contract", () => {
  it("keeps the organization boundary in browser state", () => {
    expect(parseSessionPrincipal({
      subject: "operator@example.test",
      role: "operator",
      organization_id: "acme-survey",
      expires_in_seconds: 3600,
    })).toEqual({
      subject: "operator@example.test",
      role: "operator",
      organization_id: "acme-survey",
      expires_in_seconds: 3600,
    });
  });

  it("rejects legacy responses without an organization", () => {
    expect(() => parseSessionPrincipal({
      subject: "operator@example.test",
      role: "operator",
    })).toThrow("Invalid authentication response at $.organization_id");
  });
});
