import { describe, expect, it } from "vitest";
import { createGsTilePersistentCache, gsTileCacheIdentity } from "./persistent-range-cache";

describe("persistent GSTile identity", () => {
  const principal = { organization_id: "org", subject: "user", role: "viewer" };
  it("does not persist anonymous sessions", () => {
    expect(gsTileCacheIdentity(null)).toBeNull();
    expect(createGsTilePersistentCache(null)).toBeNull();
  });
  it.each(["organization_id", "subject", "role"] as const)("isolates changes to %s", field => {
    expect(gsTileCacheIdentity({ ...principal, [field]: "other" })).not.toBe(gsTileCacheIdentity(principal));
  });
  it("does not collide when fields contain separators", () => {
    expect(gsTileCacheIdentity({ ...principal, organization_id: "a:b", subject: "c" }))
      .not.toBe(gsTileCacheIdentity({ ...principal, organization_id: "a", subject: "b:c" }));
  });
});
