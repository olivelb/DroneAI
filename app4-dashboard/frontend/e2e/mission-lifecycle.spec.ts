import { expect, test, type Page } from "@playwright/test";

type ApiOptions = {
  missionStatus?: "processing" | "cancelled";
  onMissionLaunch?: (payload: Record<string, unknown>) => void;
  onMissionCancel?: (volId: string) => void;
};

const json = (body: unknown) => ({
  status: 200,
  contentType: "application/json",
  body: JSON.stringify(body),
});

async function mockApi(page: Page, options: ApiOptions = {}) {
  await page.route("http://127.0.0.1:30080/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());

    if (url.pathname === "/auth/session") {
      await route.fulfill(json({ subject: "e2e-operator", role: "operator" }));
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
      const missions = status
        ? [{
            vol_id: "mission-existing",
            services: {
              COLMAP: {
                vol_id: "mission-existing",
                service: "COLMAP",
                step: status === "cancelled" ? "CANCELLED" : "MAPPING",
                progress: status === "cancelled" ? 0 : 42,
                status,
              },
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
    work_drive: "local",
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
