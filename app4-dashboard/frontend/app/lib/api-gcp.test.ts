import { afterEach, describe, expect, it, vi } from "vitest";

import { importGroundControl, updateGroundControlObservation } from "./api";

const jsonResponse = (payload: unknown, status = 200) => new Response(
  JSON.stringify(payload),
  { status, headers: { "Content-Type": "application/json" } },
);

describe("ground-control API", () => {
  afterEach(() => vi.restoreAllMocks());

  it("submits import metadata and the original survey file as multipart data", async () => {
    const upload = new Blob(["id,x,y,z\nP1,1,2,3\n"], { type: "text/csv" });
    Object.defineProperty(upload, "name", { value: "markers.csv" });
    vi.stubGlobal("fetch", vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      expect(init?.method).toBe("POST");
      expect(init?.headers).toBeUndefined();
      const body = init?.body as FormData;
      const storedUpload = body.get("upload") as File;
      expect(storedUpload.size).toBe(upload.size);
      expect(storedUpload.type).toBe(upload.type);
      expect(storedUpload.name).toBe("markers.csv");
      expect(body.get("source_crs")).toBe("EPSG:2154");
      expect(body.get("default_role")).toBe("checkpoint");
      expect(body.get("candidate_radius_m")).toBe("300");
      expect(body.get("column_profile")).toBe("trimble");
      return jsonResponse({ gcp_set: {}, candidate_generation: {} }, 201);
    }));

    await importGroundControl("mission 1", upload as File, {
      name: "Survey",
      sourceCrs: "EPSG:2154",
      defaultRole: "checkpoint",
      horizontalAccuracyM: 0.02,
      verticalAccuracyM: 0.03,
      imageAccuracyPx: 1,
      candidateRadiusM: 300,
      maxCandidates: 12,
      columnProfile: "trimble",
    });
  });

  it("sends native image pixels with the optimistic-lock version", async () => {
    vi.stubGlobal("fetch", vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      expect(JSON.parse(String(init?.body))).toEqual({
        status: "marked",
        pixel_x: 123.5,
        pixel_y: 456.25,
        version: 4,
      });
      return jsonResponse({ observation_id: "obs-1" });
    }));

    await updateGroundControlObservation("mission", "obs-1", {
      status: "marked",
      pixel_x: 123.5,
      pixel_y: 456.25,
      version: 4,
    });
  });
});
