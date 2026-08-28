import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchMissionCatalog } from "./api";
import { ResponseContractError } from "./contract-decoder";
import {
  parseGcpCandidateRefresh,
  parseGcpCollection,
  parseGcpObservation,
} from "./gcp-api-contracts";
import {
  parseAnalysisList,
  parseFeatureCollection,
  parseRasterMetadata,
  parseRasterStyleList,
} from "./map-api-contracts";
import {
  parseMissionCatalog,
  parseMissionDetail,
  parseParameterConfig,
  parseStatusPayload,
} from "./mission-api-contracts";
import {
  parseDirectUploadSession,
  parseSignedUploadPart,
  parseUploadFileCompletion,
  parseUploadResult,
} from "./upload-api-contracts";

const setSummary = {
  set_id: "set-1",
  name: "Survey control",
  source_filename: "markers.csv",
  source_format: "delimited-text",
  source_crs: "EPSG:2154",
  source_sha256: "a".repeat(64),
  point_count: 0,
  adjustment_count: 0,
  checkpoint_count: 0,
  marked_observation_count: 0,
  version: 1,
  created_at: "2026-08-12T00:00:00Z",
  updated_at: "2026-08-12T00:00:00Z",
};

describe("runtime API response contracts", () => {
  afterEach(() => vi.restoreAllMocks());

  it("rejects retired reconstruction pipelines in mission responses", () => {
    expect(() => parseMissionCatalog({
      items: [{
        vol_id: "old-mission",
        owner_subject: "operator",
        status: "success",
        progress: 100,
        pipeline: "legacy",
        attempt_count: 1,
        overall_status: "success",
        is_stale: false,
      }],
      total: 1,
      limit: 25,
      offset: 0,
    })).toThrow(ResponseContractError);
  });

  it("accepts the mission, pod and parameter catalog shapes used by the UI", () => {
    expect(parseMissionCatalog({
      items: [],
      total: 0,
      limit: 25,
      offset: 0,
    }).items).toEqual([]);
    expect(parseMissionDetail({
      vol_id: "mission-1",
      owner_subject: "operator@example.test",
      status: "processing",
      progress: 42,
      pipeline: "modern",
      attempt_count: 1,
      overall_status: "processing",
      is_stale: false,
      parameters: {},
      attempts: [0],
      phases: {
        COLMAP: {
          vol_id: "mission-1",
          service: "COLMAP",
          status: "processing",
        },
      },
      heartbeat: { updated_at: null, age_seconds: 1, delayed: false },
      logs: [{ message: null, details: {} }],
      products: [],
    }).vol_id).toBe("mission-1");
    expect(parseParameterConfig({
      pipelines: { modern: {} },
      processes: [],
      metadata: {},
      quality_profiles: [
        {
          id: "normal-v3",
          version: 3,
          name: "Normal",
          description: "Balanced",
          parameters: {},
        },
        {
          id: "high-quality-v4",
          version: 4,
          name: "High Quality",
          description: "Qualified production profile",
          parameters: { gs_resident_partitioning: true },
        },
      ],
      quality_profile_default: "normal-v3",
      yolo_models: [],
      sam3: {
        model_id: "facebook/sam3",
        model_revision: "revision",
        processor_target_size: 1008,
        maximum_source_tile_size: 1536,
        inference_batch_size: 1,
        minimum_vram_gib: 16,
      },
      stage_dag: { version: 1, stages: [] },
    }).sam3.model_id).toBe("facebook/sam3");
  });

  it("accepts map, GCP and upload response shapes", () => {
    expect(parseRasterMetadata({
      bounds: { wgs84: [2, 48, 2.1, 48.1] },
      bands: 3,
      min_zoom: 10,
      max_zoom: 20,
    }).bands).toBe(3);
    expect(parseFeatureCollection({
      type: "FeatureCollection",
      features: [{
        type: "Feature",
        id: "feature-1",
        geometry: { type: "Point", coordinates: [2.1, 48.1] },
        properties: {},
      }],
    }).features).toHaveLength(1);
    expect(parseAnalysisList({ runs: [] }).runs).toEqual([]);
    expect(parseRasterStyleList({ layer: "ortho", styles: [] }).styles).toEqual([]);
    expect(parseGcpCollection({
      type: "FeatureCollection",
      features: [],
      gcp_sets: [setSummary],
    }).gcp_sets).toHaveLength(1);
    expect(parseGcpCandidateRefresh({
      gcp_set: {
        ...setSummary,
        type: "FeatureCollection",
        features: [],
      },
      candidate_generation: { added_observation_count: 0 },
    }).candidate_generation.added_observation_count).toBe(0);
    expect(parseGcpObservation({
      observation_id: "obs-1",
      image_name: "DJI_0001.JPG",
      status: "candidate",
      version: 1,
      updated_at: "2026-08-12T00:00:00Z",
    }).status).toBe("candidate");
    expect(parseDirectUploadSession({
      session_id: "upload-1",
      dataset: "survey",
      status: "uploading",
      total: 0,
      total_bytes: 0,
      part_size: 16_777_216,
      expires_at: "2026-08-13T00:00:00Z",
      files: [],
    }).session_id).toBe("upload-1");
    expect(parseSignedUploadPart({
      method: "PUT",
      url: "https://objects.example/part",
      expires_in: 900,
      part_number: 1,
      expected_size: 1024,
    }).part_number).toBe(1);
    expect(parseUploadFileCompletion({
      file_id: "file-1",
      name: "image.jpg",
      s3_key: "organizations/acme/datasets/survey/image.jpg",
      size: 42,
      etag: "etag",
      status: "completed",
    }).status).toBe("completed");
    expect(parseUploadResult({
      total: 1,
      completed: 1,
      failed: 0,
      status: "completed",
    }).completed).toBe(1);
  });

  it("rejects malformed HTTP and websocket payloads with a field path", async () => {
    expect(() => parseStatusPayload({ service: "COLMAP" })).toThrow(
      "Invalid status event response at $.vol_id",
    );
    expect(() => parseRasterMetadata({
      bounds: { wgs84: [2, 48, 2.1] },
      bands: 3,
      min_zoom: 10,
      max_zoom: 20,
    })).toThrow("$.bounds.wgs84");

    vi.stubGlobal("fetch", vi.fn(async () => new Response(
      JSON.stringify({ items: "not-an-array", total: 1, limit: 25, offset: 0 }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    )));
    await expect(fetchMissionCatalog()).rejects.toBeInstanceOf(
      ResponseContractError,
    );
  });
});
