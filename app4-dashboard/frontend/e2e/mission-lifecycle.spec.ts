import { expect, test, type Page } from "@playwright/test";

type ApiOptions = {
  missionStatus?: "processing" | "cancelled" | "success";
  sessionAuthenticated?: boolean;
  onSessionCreate?: (apiKey: string) => void;
  onMissionLaunch?: (payload: Record<string, unknown>) => void;
  onMissionCancel?: (volId: string) => void;
  onMapExport?: (url: string) => void;
};

const json = (body: unknown, status = 200) => ({
  status,
  contentType: "application/json",
  body: JSON.stringify(body),
});

async function mockApi(page: Page, options: ApiOptions = {}) {
  await page.route("http://127.0.0.1:30080/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());

    if (url.pathname === "/auth/session" && request.method() === "GET") {
      if (options.sessionAuthenticated === false) {
        await route.fulfill(json({ detail: "Session expired" }, 401));
        return;
      }
      await route.fulfill(json({ subject: "e2e-operator", role: "operator" }));
      return;
    }
    if (url.pathname === "/auth/session" && request.method() === "POST") {
      const payload = request.postDataJSON() as { api_key: string };
      options.onSessionCreate?.(payload.api_key);
      await route.fulfill(json({ subject: "e2e-operator", role: "operator" }));
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
        work_drives: [
          { name: "local", label: "Local", mount: "/work/local" },
        ],
        work_drive_default: "local",
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
  });
});

test("the English default can be switched to persistent French", async ({ page }) => {
  await mockApi(page);

  await page.goto("/");
  await expect(page.getByRole("button", { name: /1\. Prepare/ })).toBeVisible();
  await page.getByRole("combobox", { name: "Language" }).selectOption("fr");
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
      vol_id: "mission-websocket",
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
