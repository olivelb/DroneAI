import { describe, expect, it } from "vitest";

import { SERVICE_ORDER, serviceOrderFor } from "./types";

describe("serviceOrderFor", () => {
  it("keeps the complete map pipeline", () => {
    expect(serviceOrderFor({})).toEqual(SERVICE_ORDER);
    expect(serviceOrderFor({
      COLMAP: {
        vol_id: "map-1",
        details: { process: "map", terminal: true },
      },
    })).toEqual(SERVICE_ORDER);
  });

  it("ends a qualified facade after COLMAP", () => {
    expect(serviceOrderFor({
      COLMAP: {
        vol_id: "facade-1",
        details: { process: "facade", terminal: true },
      },
    })).toEqual(["COLMAP"]);
  });
});
