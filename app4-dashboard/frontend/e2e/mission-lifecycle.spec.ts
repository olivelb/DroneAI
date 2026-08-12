import { expect, test, type Page } from "@playwright/test";

type ApiOptions = {
  missionStatus?: "processing" | "cancelled" | "success";
  sessionAuthenticated?: boolean;
  onSessionCreate?: (apiKey: string) => void;
  onMissionLaunch?: (payload: Record<string, unknown>) => void;
  onMissionCancel?: (volId: string) => void;
  onMapExport?: (url: string) => void;
  onGcpPointUpdate?: (payload: Record<string, unknown>) => void;
  onGcpObservationUpdate?: (payload: Record<string, unknown>) => void;
  onGcpCandidateRefresh?: () => void;
  onGcpBundle?: () => void;
};

const json = (body: unknown, status = 200) => ({
  status,
  contentType: "application/json",
  body: JSON.stringify(body),
});

const gcpSetSummary = {
  set_id: "set-1",
  name: "Survey control",
  source_filename: "markers.csv",
  source_format: "delimited-text",
  source_crs: "EPSG:2154",
  source_sha256: "a".repeat(64),
  point_count: 1,
  adjustment_count: 1,
  checkpoint_count: 0,
  marked_observation_count: 0,
  version: 1,
  created_at: "2026-08-10T12:00:00Z",
  updated_at: "2026-08-10T12:00:00Z",
};

const gcpObservation = (overrides: Record<string, unknown> = {}) => ({
  observation_id: "obs-1",
  image_name: "DJI_0001.JPG",
  image_s3_key: "datasets/survey-set/DJI_0001.JPG",
  status: "candidate",
  pixel_x: null,
  pixel_y: null,
  candidate_distance_m: 18.5,
  candidate_method: "exif-distance",
  projected_pixel_x: null,
  projected_pixel_y: null,
  image_width_px: 1200,
  image_height_px: 800,
  image_longitude: 2.0501,
  image_latitude: 48.0501,
  version: 1,
  updated_at: "2026-08-10T12:00:00Z",
  ...overrides,
});

const gcpFeature = (overrides: Record<string, unknown> = {}) => ({
  type: "Feature",
  id: "point-1",
  geometry: { type: "Point", coordinates: [2.05, 48.05] },
  properties: {
    point_id: "point-1",
    set_id: "set-1",
    set_name: "Survey control",
    external_id: "P1",
    altitude_m: 125,
    source_coordinates: [652000, 6860000, 125],
    role: "adjustment",
    horizontal_accuracy_m: 0.02,
    vertical_accuracy_m: 0.03,
    image_accuracy_px: 1,
    observation_summary: { candidate: 1, marked: 0, skipped: 0 },
    observations: [gcpObservation()],
    properties: {},
    version: 1,
    updated_at: "2026-08-10T12:00:00Z",
    ...overrides,
  },
});

async function mockApi(page: Page, options: ApiOptions = {}) {
  const missionStatus = options.missionStatus ?? "success";
  const missionStep =
    missionStatus === "cancelled"
      ? "CANCELLED"
      : missionStatus === "success"
        ? "DONE"
        : "MAPPING";
  const missionProgress =
    missionStatus === "success" ? 100 : missionStatus === "processing" ? 42 : 0;

  await page.route("http://127.0.0.1:30080/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());

    if (url.pathname === "/auth/session" && request.method() === "GET") {
      if (options.sessionAuthenticated === false) {
        await route.fulfill(json({ detail: "Session expired" }, 401));
        return;
      }
      await route.fulfill(json({
        subject: "e2e-operator",
        role: "operator",
        organization_id: "e2e-organization",
      }));
      return;
    }
    if (url.pathname === "/auth/session" && request.method() === "POST") {
      const payload = request.postDataJSON() as { api_key: string };
      options.onSessionCreate?.(payload.api_key);
      await route.fulfill(json({
        subject: "e2e-operator",
        role: "operator",
        organization_id: "e2e-organization",
      }));
      return;
    }
    if (url.pathname === "/auth/session" && request.method() === "DELETE") {
      await route.fulfill(json({ status: "success" }));
      return;
    }
    if (url.pathname === "/browse") {
      await route.fulfill(json([
        {
          name: "survey-set",
          path: "datasets/survey-set",
          is_dir: true,
          image_count: 24,
        },
      ]));
      return;
    }
    if (url.pathname === "/mission/parameters") {
      await route.fulfill(json({
        pipelines: {
          modern: { orthophoto_mode: "map" },
          legacy: { orthophoto_mode: "map" },
        },
        processes: [
          {
            id: "map",
            label: "Map",
            description: "Aerial map",
            stages: ["COLMAP", "TILER", "IA"],
            parameters: { orthophoto_mode: "map" },
          },
        ],
        metadata: {},
        quality_profile_default: "normal-v1",
        quality_profiles: [
          {
            id: "fast-v1",
            version: 1,
            name: "Fast",
            description: "Fast production profile",
            parameters: {
              feature_max_image_size: "1600",
              feature_max_num_features: "2048",
              gs_iterations: "7500",
              gs_cap_max: "1500000",
            },
          },
          {
            id: "normal-v1",
            version: 1,
            name: "Normal",
            description: "Balanced production profile",
            parameters: {
              feature_max_image_size: "2400",
              feature_max_num_features: "4096",
              gs_iterations: "15000",
              gs_cap_max: "3000000",
            },
          },
          {
            id: "high-quality-v1",
            version: 1,
            name: "High Quality",
            description: "High quality production profile",
            parameters: {
              feature_max_image_size: "4096",
              feature_max_num_features: "16384",
              gs_iterations: "30000",
              gs_cap_max: "5000000",
            },
          },
        ],
        yolo_models: [
          {
            id: "yolo26l",
            label: "YOLO26L",
            available: true,
            artifact: "yolo26l-obb.pt",
            repository: "ultralytics/assets",
            revision: "v8.4.0",
            artifact_sha256: "a".repeat(64),
            classes: ["small vehicle", "large vehicle"],
            selectable_classes: ["car", "small vehicle", "large vehicle"],
          },
        ],
        sam3: {
          model_id: "facebook/sam3",
          model_revision: "revision",
          processor_target_size: 1008,
          maximum_source_tile_size: 1536,
          inference_batch_size: 1,
          minimum_vram_gib: 16,
        },
        work_drives: [
          { name: "local", label: "Local", mount: "/work/local" },
        ],
        work_drive_default: "local",
        stage_dag: {
          version: 1,
          stages: [
            { id: "reconstruction", dependencies: [] },
            { id: "gaussian_training", dependencies: ["reconstruction"] },
            { id: "gaussian_filtering", dependencies: ["gaussian_training"] },
            { id: "rasterization", dependencies: ["gaussian_filtering"] },
            { id: "detection", dependencies: ["rasterization"] },
          ],
        },
      }));
      return;
    }
    if (url.pathname === "/pods") {
      await route.fulfill(json({ pods: [], error: null }));
      return;
    }
    if (url.pathname === "/status/summary") {
      const status = options.missionStatus;
      const terminalSuccess = status === "success";
      const missions = status
        ? [{
            vol_id: "mission-existing",
            services: {
              COLMAP: {
                vol_id: "mission-existing",
                service: "COLMAP",
                step: status === "cancelled" ? "CANCELLED" : terminalSuccess ? "DONE" : "MAPPING",
                progress: terminalSuccess ? 100 : status === "cancelled" ? 0 : 42,
                status,
              },
              ...(terminalSuccess
                ? {
                    TILER: {
                      vol_id: "mission-existing",
                      service: "TILER",
                      step: "DONE",
                      progress: 100,
                      status: "success",
                    },
                    IA: {
                      vol_id: "mission-existing",
                      service: "IA",
                      step: "DONE",
                      progress: 100,
                      status: "success",
                    },
                  }
                : {}),
            },
            logs: [],
            updated_at: 1_800_000_000,
            overall_status: status,
          }]
        : [];
      await route.fulfill(json({
        active_vol_id: missions[0]?.vol_id ?? null,
        missions,
      }));
      return;
    }
    if (url.pathname === "/maps/mission-existing/analyses") {
      await route.fulfill(json({ runs: [] }));
      return;
    }
    if (url.pathname === "/maps/mission-existing/metadata/ortho") {
      await route.fulfill(json({
        bounds: { wgs84: [2.0, 48.0, 2.1, 48.1] },
        bands: 3,
        min_zoom: 10,
        max_zoom: 20,
        crs: "EPSG:2154",
      }));
      return;
    }
    if (url.pathname === "/maps/mission-existing/vectors.geojson") {
      await route.fulfill(json({
        type: "FeatureCollection",
        features: [],
      }));
      return;
    }
    if (url.pathname === "/maps/mission-existing/styles/ortho") {
      await route.fulfill(json({ layer: "ortho", styles: [] }));
      return;
    }
    if (url.pathname === "/maps/mission-existing/gcps" && request.method() === "GET") {
      await route.fulfill(json({
        type: "FeatureCollection",
        gcp_sets: [gcpSetSummary],
        features: [gcpFeature()],
      }));
      return;
    }
    if (url.pathname === "/maps/mission-existing/gcps/points/point-1") {
      const payload = request.postDataJSON() as Record<string, unknown>;
      options.onGcpPointUpdate?.(payload);
      await route.fulfill(json(gcpFeature({ ...payload, version: 2 })));
      return;
    }
    if (url.pathname === "/maps/mission-existing/gcps/observations/obs-1") {
      const payload = request.postDataJSON() as Record<string, unknown>;
      options.onGcpObservationUpdate?.(payload);
      await route.fulfill(json(gcpObservation({ ...payload, version: 2 })));
      return;
    }
    if (url.pathname === "/maps/mission-existing/gcps/set-1/candidates/refresh") {
      options.onGcpCandidateRefresh?.();
      await route.fulfill(json({
        gcp_set: {
          ...gcpSetSummary,
          type: "FeatureCollection",
          features: [gcpFeature()],
        },
        candidate_generation: { added_observation_count: 0 },
      }));
      return;
    }
    if (url.pathname === "/maps/mission-existing/gcps/set-1/bundle") {
      options.onGcpBundle?.();
      await route.fulfill(json({
        schema_version: 1,
        set_id: "set-1",
        source_sha256: "a".repeat(64),
        gcp_list: {
          key: `blobs/sha256/aa/${"a".repeat(64)}`,
          size: 10,
          sha256: "a".repeat(64),
        },
        accuracy_csv: {
          key: `blobs/sha256/bb/${"b".repeat(64)}`,
          size: 10,
          sha256: "b".repeat(64),
        },
        quality: {
          adjustment_points: 3,
          checkpoint_points: 1,
          marked_observations: 8,
          verification: "independent-checkpoints",
        },
      }));
      return;
    }
    if (url.pathname === "/maps/mission-existing/gcps/set-1/audit") {
      await route.fulfill(json({
        set_id: "set-1",
        events: [{
          event_id: "audit-1",
          action: "imported",
          actor_subject: "e2e-operator",
          point_id: null,
          observation_id: null,
          before_state: null,
          after_state: { point_count: 1 },
          created_at: "2026-08-10T12:00:00Z",
        }],
      }));
      return;
    }
    if (url.pathname === "/files/datasets/survey-set/DJI_0001.JPG") {
      await route.fulfill({
        status: 200,
        contentType: "image/svg+xml",
        body: '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="800"><rect width="1200" height="800" fill="#526b62"/><circle cx="600" cy="400" r="28" fill="white"/><path d="M560 400h80M600 360v80" stroke="red" stroke-width="4"/></svg>',
      });
      return;
    }
    if (url.pathname === "/maps/mission-existing/export/vectors") {
      options.onMapExport?.(url.toString());
      await route.fulfill({
        status: 200,
        contentType: "application/geopackage+sqlite3",
        headers: {
          "Content-Disposition": "attachment; filename=mission-existing.gpkg",
        },
        body: "e2e-geopackage",
      });
      return;
    }
    if (url.pathname === "/missions") {
      await route.fulfill(json({
        items: [{
          vol_id: "mission-existing",
          owner_subject: "e2e-operator",
          status: missionStatus,
          current_step: missionStep,
          progress: missionProgress,
          pipeline: "modern",
          quality_profile: "normal-v1",
          attempt_count: 1,
          updated_at: "2026-08-09T12:00:00Z",
          overall_status: missionStatus,
          is_stale: false,
        }],
        total: 1,
        limit: 20,
        offset: 0,
      }));
      return;
    }
    if (url.pathname === "/missions/mission-existing") {
      await route.fulfill(json({
        vol_id: "mission-existing",
        owner_subject: "e2e-operator",
        status: missionStatus,
        current_step: missionStep,
        progress: missionProgress,
        pipeline: "modern",
        quality_profile: "normal-v1",
        attempt_count: 1,
        updated_at: "2026-08-09T12:00:00Z",
        overall_status: missionStatus,
        is_stale: false,
        parameters: { quality_profile: "normal-v1" },
        attempts: [{ attempt: 0, status: missionStatus }],
        phases: {
          COLMAP: {
            vol_id: "mission-existing",
            status: missionStatus,
            step: missionStep,
            progress: missionProgress,
          },
        },
        heartbeat: { updated_at: "2026-08-09T12:00:00Z", age_seconds: 2, delayed: false },
        logs: [{
          service: "COLMAP",
          step: missionStep,
          status: missionStatus,
          message:
            missionStatus === "success"
              ? "Published"
              : `Mission ${missionStatus}`,
        }],
        products: [{ kind: "orthomosaic", s3_key: "missions/mission-existing/orthomosaic.tif" }],
      }));
      return;
    }
    if (url.pathname === "/mission" && request.method() === "POST") {
      options.onMissionLaunch?.(request.postDataJSON());
      await route.fulfill(json({ status: "success", vol_id: "mission-e2e" }));
      return;
    }
    if (url.pathname === "/mission/cancel" && request.method() === "POST") {
      options.onMissionCancel?.(url.searchParams.get("vol_id") ?? "");
      await route.fulfill(json({ status: "success" }));
      return;
    }

    await route.fulfill(json({}));
  });
}

test("an operator selects a dataset and launches a mission", async ({ page }) => {
  let launched: Record<string, unknown> | undefined;
  await mockApi(page, { onMissionLaunch: (payload) => { launched = payload; } });

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "DroneAI" })).toBeVisible();
  await page.getByTitle("Select as input dataset").click();
  await page.getByLabel("Mission ID").fill("mission-e2e");
  await page.getByRole("button", { name: "Launch pipeline" }).click();

  await expect.poll(() => launched).toMatchObject({
    vol_id: "mission-e2e",
    input_dataset: "datasets/survey-set",
    pipeline: "modern",
    quality_profile: "normal-v1",
    work_drive: "local",
    phases: [
      "reconstruction",
      "gaussian_training",
      "gaussian_filtering",
      "rasterization",
      "detection",
    ],
  });
});

test("the owner-scoped catalogue opens a durable mission detail", async ({ page }) => {
  await mockApi(page);

  await page.goto("/missions");
  await expect(page.getByRole("heading", { name: "Mission catalogue" })).toBeVisible();
  await expect(page.getByText("mission-existing")).toBeVisible();
  await page.getByRole("link", { name: "Open details" }).click();

  await expect(page).toHaveURL(/\/missions\/mission-existing$/);
  await expect(page.getByRole("heading", { name: "mission-existing" })).toBeVisible();
  await expect(page.getByText("Published")).toBeVisible();
  await expect(page.getByText("orthomosaic", { exact: true })).toBeVisible();
});

test("the English default can be switched to persistent French", async ({ page }) => {
  await mockApi(page);

  await page.goto("/");
  await expect(page.getByRole("button", { name: /1\. Prepare/ })).toBeVisible();
  await page.getByRole("button", { name: /2\. Align/ }).click();
  await expect(
    page.getByRole("heading", { name: "Reconstruction and alignment" }),
  ).toBeVisible();
  await expect(page.getByText("Production process", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: /3\. Produce/ }).click();
  await expect(
    page.getByRole("heading", { name: "DroneGS and orthomosaic" }),
  ).toBeVisible();
  await expect(page.getByText("Effective budget", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: /4\. Detect/ }).click();
  await expect(
    page.getByRole("heading", { name: "Tiling and detection" }),
  ).toBeVisible();
  await expect(page.getByText("Inference strategy", { exact: true })).toBeVisible();
  await expect(page.getByText("Stratégie d’inférence", { exact: true })).toHaveCount(0);

  await page.getByRole("combobox", { name: "Language" }).selectOption("fr");
  await expect(
    page.getByRole("heading", { name: "Tuilage et détection" }),
  ).toBeVisible();
  await expect(page.getByText("Stratégie d’inférence", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: /1\. Préparer/ })).toBeVisible();

  await page.reload();
  await expect(page.getByRole("button", { name: /1\. Préparer/ })).toBeVisible();
});

test("a versioned quality profile applies its effective parameters", async ({ page }) => {
  let launched: Record<string, unknown> | undefined;
  await mockApi(page, { onMissionLaunch: (payload) => { launched = payload; } });

  await page.goto("/");
  await page.getByTitle("Select as input dataset").click();
  await page.getByLabel("Mission ID").fill("mission-fast-profile");
  await page.getByRole("button", { name: /2\. Align/ }).click();
  await page.getByRole("button", { name: /Fast fast-v1/ }).click();
  await page.getByRole("button", { name: "Launch pipeline" }).click();

  await expect.poll(() => launched).toMatchObject({
    quality_profile: "fast-v1",
    colmap_params: {
      feature_max_image_size: "1600",
      feature_max_num_features: "2048",
      gs_iterations: "7500",
      gs_cap_max: "1500000",
    },
  });
});

test("a running mission can be cancelled and remains distinct from failure", async ({ page }) => {
  let cancelledMission = "";
  await mockApi(page, {
    missionStatus: "processing",
    onMissionCancel: (volId) => { cancelledMission = volId; },
  });

  await page.goto("/");
  await page.getByRole("button", { name: "Stop mission" }).click();

  await expect.poll(() => cancelledMission).toBe("mission-existing");
});

test("a cancelled mission is rendered as terminal, not running", async ({ page }) => {
  await mockApi(page, { missionStatus: "cancelled" });

  await page.goto("/");
  await expect(page.getByText("cancelled", { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Stop mission" })).toHaveCount(0);
});

test("an expired browser session can be renewed with an API credential", async ({ page }) => {
  let submittedCredential = "";
  await mockApi(page, {
    sessionAuthenticated: false,
    onSessionCreate: (apiKey) => { submittedCredential = apiKey; },
  });

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Operator sign-in" })).toBeVisible();
  await page.getByLabel("API credential").fill("e2e-operator-key-with-at-least-32-characters");
  await page.getByRole("button", { name: "Open Mission Studio" }).click();

  await expect(page.getByRole("heading", { name: "DroneAI" })).toBeVisible();
  expect(submittedCredential).toBe("e2e-operator-key-with-at-least-32-characters");
});

test("live mission updates recover after a WebSocket disconnect", async ({ page }) => {
  let connectionCount = 0;
  await page.routeWebSocket("ws://127.0.0.1:30080/ws/status", (socket) => {
    connectionCount += 1;
    if (connectionCount === 1) {
      setTimeout(() => void socket.close({ code: 1012, reason: "e2e restart" }), 50);
      return;
    }
    setTimeout(() => socket.send(JSON.stringify({
      vol_id: "mission-existing",
      service: "COLMAP",
      step: "MAPPING",
      progress: 17,
      status: "processing",
      log: "WebSocket reconnect confirmed",
    })), 50);
  });
  await mockApi(page);

  await page.goto("/");
  await expect.poll(() => connectionCount).toBe(2);
  await expect(page.getByText("Live", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Open mission monitor" }).click();
  await expect(page.getByText("WebSocket reconnect confirmed")).toBeVisible();
});

test("a completed mission exports its vectors as a projected GeoPackage", async ({ page }) => {
  let exportedUrl = "";
  await mockApi(page, {
    missionStatus: "success",
    onMapExport: (url) => { exportedUrl = url; },
  });
  await page.addInitScript(() => {
    Object.defineProperty(window, "showSaveFilePicker", {
      configurable: true,
      value: async () => ({
        createWritable: async () => new WritableStream(),
      }),
    });
  });

  await page.goto("/");
  await expect(page.getByText("success", { exact: true }).first()).toBeVisible();
  await page.getByRole("button", { name: /5\. Explore/ }).click();
  await page.getByRole("button", { name: "Export", exact: true }).click();
  const exportButton = page.getByRole("button", {
    name: "Save layer",
  });
  await expect(exportButton).toBeVisible();
  await exportButton.click();

  await expect.poll(() => exportedUrl).toBeTruthy();
  const url = new URL(exportedUrl);
  expect(Object.fromEntries(url.searchParams)).toMatchObject({
    format: "gpkg",
    scope: "all",
    crs: "raster",
  });
});

test("an operator edits a GCP and marks its native image observation", async ({ page }) => {
  let pointUpdate: Record<string, unknown> | undefined;
  let observationUpdate: Record<string, unknown> | undefined;
  let candidatesRefreshed = false;
  let bundlePrepared = false;
  await mockApi(page, {
    onGcpPointUpdate: (payload) => { pointUpdate = payload; },
    onGcpObservationUpdate: (payload) => { observationUpdate = payload; },
    onGcpCandidateRefresh: () => { candidatesRefreshed = true; },
    onGcpBundle: () => { bundlePrepared = true; },
  });

  await page.goto("/");
  await page.getByRole("button", { name: /5\. Explore/ }).click();
  await page.getByRole("button", { name: "GCP", exact: true }).click();
  await expect(page.getByText("Ground-control points")).toBeVisible();
  await page.getByRole("button", { name: /P1/ }).click();
  await page.getByRole("button", { name: "Refresh nearby photos" }).click();
  await expect.poll(() => candidatesRefreshed).toBe(true);
  await page.getByRole("button", { name: "Validate for reconstruction" }).click();
  await expect.poll(() => bundlePrepared).toBe(true);
  await page.getByLabel("Longitude (X, EPSG:4326)").fill("2.0505");
  await page.getByLabel("Calculation role").selectOption("checkpoint");
  await page.getByRole("button", { name: "Save coordinates and accuracy" }).click();
  await expect.poll(() => pointUpdate).toMatchObject({
    longitude: 2.0505,
    role: "checkpoint",
    version: 1,
  });

  await page.getByRole("button", { name: /DJI_0001\.JPG/ }).click();
  await expect(page.getByText(/P1 · DJI_0001\.JPG/)).toBeVisible();
  const sourceImage = page.getByAltText("DJI_0001.JPG");
  await expect(sourceImage).toBeVisible();
  await sourceImage.click({ position: { x: 150, y: 100 } });
  await page.getByRole("button", { name: "Save mark · next" }).click();
  await expect.poll(() => observationUpdate).toMatchObject({
    status: "marked",
    version: 1,
  });
  expect(Number(observationUpdate?.pixel_x)).toBeGreaterThan(0);
  expect(Number(observationUpdate?.pixel_y)).toBeGreaterThan(0);
});
