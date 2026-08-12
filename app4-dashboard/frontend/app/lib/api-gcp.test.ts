import { afterEach, describe, expect, it, vi } from "vitest";

import { importGroundControl, updateGroundControlObservation } from "./api";

const jsonResponse = (payload: unknown, status = 200) => new Response(
  JSON.stringify(payload),
  { status, headers: { "Content-Type": "application/json" } },
);

const setSummary = {
  set_id: "set-1",
  name: "Survey",
  source_filename: "markers.csv",
  source_format: "delimited-text",
  source_crs: "EPSG:2154",
  source_sha256: "a".repeat(64),
  point_count: 1,
  adjustment_count: 0,
  checkpoint_count: 1,
  marked_observation_count: 1,
  version: 1,
  created_at: "2026-08-12T00:00:00Z",
  updated_at: "2026-08-12T00:00:00Z",
};

const observation = {
  observation_id: "obs-1",
  image_name: "DJI_0001.JPG",
  image_s3_key: "datasets/survey/DJI_0001.JPG",
  status: "marked",
  pixel_x: 123.5,
  pixel_y: 456.25,
  candidate_distance_m: 12,
  candidate_method: "camera-projection",
  projected_pixel_x: 120,
  projected_pixel_y: 450,
  image_width_px: 1200,
  image_height_px: 800,
  image_longitude: 2.1,
  image_latitude: 48.1,
  version: 5,
  updated_at: "2026-08-12T00:00:00Z",
};

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
      return jsonResponse({
        gcp_set: setSummary,
        candidate_generation: {},
      }, 201);
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
      return jsonResponse(observation);
    }));

    await updateGroundControlObservation("mission", "obs-1", {
      status: "marked",
      pixel_x: 123.5,
      pixel_y: 456.25,
      version: 4,
    });
  });
});
