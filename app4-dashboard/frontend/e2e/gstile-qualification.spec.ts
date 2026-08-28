import { expect, test, type Page } from "@playwright/test";
import { createReadStream, readFileSync, statSync } from "node:fs";
import { createServer, type Server } from "node:http";
import { isAbsolute, join, relative, resolve } from "node:path";

type ManifestPack = {
  id: string;
  path: string;
  byteLength: number;
  sha256: string;
};

type QualificationManifest = {
  bundleId: string;
  profile: string;
  source: { gaussianCount: number };
  packs: ManifestPack[];
};

const saintEtienneFacadeView = {
  kind: "facade",
  right: [0.9975430758, -0.0338857647, -0.0613153074],
  up: [-0.0358895499, -0.9988471697, -0.0318790192],
  outward: [-0.0601643763, 0.0340012737, -0.9976092227],
} as const;

const json = (body: unknown, status = 200) => ({
  status,
  contentType: "application/json",
  body: JSON.stringify(body),
});

const loadManifest = (bundleRoot: string) =>
  JSON.parse(
    readFileSync(join(bundleRoot, "manifest.json"), "utf8"),
  ) as QualificationManifest;

const missionDetail = {
  vol_id: "gstile-qualification",
  owner_subject: "e2e-operator",
  status: "success",
  current_step: "DONE",
  progress: 100,
  pipeline: "modern",
  quality_profile: "high-quality-v4",
  attempt_count: 1,
  updated_at: "2026-08-22T10:00:00Z",
  overall_status: "success",
  is_stale: false,
  parameters: { qualification: "gstile-real-bundle" },
  attempts: [{ attempt: 0, status: "success" }],
  phases: {},
  heartbeat: {
    updated_at: "2026-08-22T10:00:00Z",
    age_seconds: 0,
    delayed: false,
  },
  logs: [],
  products: [{ kind: "gaussian_viewer_bundle", status: "published" }],
};

const descriptor = (
  manifest: QualificationManifest,
  packUrl: (pack: ManifestPack) => string,
) => ({
  schema: "droneai-gaussian-viewer-descriptor",
  version: 1,
  artifactId: "gstile-qualification-artifact",
  bundleId: manifest.bundleId,
  expiresAt: "2030-01-01T00:00:00Z",
  recommendedView: saintEtienneFacadeView,
  manifest,
  packs: manifest.packs.map((pack) => ({
    id: pack.id,
    url: packUrl(pack),
    byteLength: pack.byteLength,
    sha256: pack.sha256,
  })),
});

const mockMissionApi = async (
  page: Page,
  viewerDescriptor: ReturnType<typeof descriptor>,
) => {
  await page.route("http://127.0.0.1:30080/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/auth/session") {
      await route.fulfill(
        json({
          subject: "e2e-operator",
          role: "operator",
          organization_id: "e2e-organization",
        }),
      );
      return;
    }
    if (url.pathname === "/browse") {
      await route.fulfill(json([]));
      return;
    }
    if (url.pathname === "/missions") {
      await route.fulfill(
        json({ items: [missionDetail], total: 1, limit: 100, offset: 0 }),
      );
      return;
    }
    if (url.pathname === "/missions/gstile-qualification/gaussians/viewer") {
      await route.fulfill(json(viewerDescriptor));
      return;
    }
    if (url.pathname === "/missions/gstile-qualification") {
      await route.fulfill(json(missionDetail));
      return;
    }
    await route.fulfill(json({ detail: `Unexpected qualification API ${url.pathname}` }, 404));
  });
};

const startRangeServer = async (
  bundleRoot: string,
  manifest: QualificationManifest,
) => {
  const root = resolve(bundleRoot);
  let rangeRequestCount = 0;
  const packs = new Map(
    manifest.packs.map((pack) => [`/${pack.path}`, { pack, file: resolve(root, pack.path) }]),
  );
  for (const { pack, file } of packs.values()) {
    const relativeFile = relative(root, file);
    if (
      relativeFile.startsWith("..") ||
      isAbsolute(relativeFile) ||
      statSync(file).size !== pack.byteLength
    ) {
      throw new Error(`GSTile qualification pack ${pack.id} is unsafe or incomplete`);
    }
  }

  const server = createServer((request, response) => {
    response.setHeader("Access-Control-Allow-Origin", "http://127.0.0.1:3000");
    response.setHeader("Access-Control-Allow-Headers", "Range");
    response.setHeader("Access-Control-Expose-Headers", "Content-Range, Accept-Ranges");
    response.setHeader("Accept-Ranges", "bytes");
    if (request.method === "OPTIONS") {
      response.writeHead(204);
      response.end();
      return;
    }
    const pathname = new URL(request.url ?? "/", "http://127.0.0.1").pathname;
    const entry = packs.get(pathname);
    const match = request.headers.range?.match(/^bytes=(\d+)-(\d+)$/);
    if (!entry) {
      response.writeHead(404);
      response.end();
      return;
    }
    if (!match) {
      response.writeHead(416, { "Content-Range": `bytes */${entry.pack.byteLength}` });
      response.end();
      return;
    }
    const start = Number(match[1]);
    const end = Number(match[2]);
    if (start < 0 || end < start || end >= entry.pack.byteLength) {
      response.writeHead(416, { "Content-Range": `bytes */${entry.pack.byteLength}` });
      response.end();
      return;
    }
    rangeRequestCount += 1;
    response.writeHead(206, {
      "Content-Type": "application/octet-stream",
      "Content-Length": end - start + 1,
      "Content-Range": `bytes ${start}-${end}/${entry.pack.byteLength}`,
    });
    createReadStream(entry.file, { start, end }).pipe(response);
  });
  await new Promise<void>((resolveListening, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolveListening);
  });
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("Range server failed");
  return {
    server,
    origin: `http://127.0.0.1:${address.port}`,
    rangeRequestCount: () => rangeRequestCount,
  };
};

const closeServer = (server: Server) =>
  new Promise<void>((resolveClose, reject) => {
    server.close((error) => (error ? reject(error) : resolveClose()));
    server.closeAllConnections();
  });

for (const assemblyMode of ["worker", "main-thread", "retired-query"] as const) {
test("streams a current V4 bundle with " + assemblyMode + " assembly", async ({
  page,
}, testInfo) => {
  const bundleRoot = process.env.GSTILE_BUNDLE_ROOT;
  test.skip(!bundleRoot, "GSTILE_BUNDLE_ROOT is required for real-bundle qualification");
  test.setTimeout(3 * 60_000);
  const manifest = loadManifest(bundleRoot!);
  expect(manifest.profile).toBe("dronegs-sh3-opacity-sh3-q96-adaptive-lod-v4");
  const { server, origin, rangeRequestCount } = await startRangeServer(
    bundleRoot!,
    manifest,
  );
  try {
    await mockMissionApi(
      page,
      descriptor(manifest, (pack) => `${origin}/${pack.path}`),
    );
    // Probe on a secure local origin before loading any scene.
    await page.goto("/");
    const adapterInfo = await page.evaluate(async () => {
      const gpu = (
        navigator as Navigator & {
          gpu?: {
            requestAdapter(options: {
              powerPreference: "high-performance";
            }): Promise<{
              info: {
                vendor: string;
                architecture: string;
                device: string;
                description: string;
                isFallbackAdapter?: boolean;
              };
            } | null>;
          };
        }
      ).gpu;
      if (!gpu) return null;
      const adapter = await gpu.requestAdapter({
        powerPreference: "high-performance",
      });
      return adapter
        ? {
            vendor: adapter.info.vendor,
            architecture: adapter.info.architecture,
            device: adapter.info.device,
            description: adapter.info.description,
            isFallbackAdapter: adapter.info.isFallbackAdapter ?? false,
          }
        : null;
    });
    expect(adapterInfo).not.toBeNull();
    expect(adapterInfo?.isFallbackAdapter, "A hardware WebGPU adapter is required").toBe(false);
    expect(JSON.stringify(adapterInfo), "Software adapters cannot qualify images")
      .not.toMatch(/swiftshader|llvmpipe|lavapipe|software|microsoft basic render/i);
    await testInfo.attach("webgpu-adapter", {
      body: JSON.stringify({ adapterInfo, browser: page.context().browser()?.version(), platform: process.platform }),
      contentType: "application/json",
    });
    await page.goto(
      "/missions/gstile-qualification" +
      (assemblyMode === "main-thread" ? "?gstileWorkerAssembly=0" :
        assemblyMode === "retired-query"
          ? "?gstileOpacity=base&gstileSort=cpu&gstileTransform=float32&gstileMaxScale=0.000001&gstileCoverage=0&gstileSiblingLeaves=1&gstileRadialSort=1&gstileGpuAssembly=tiled"
          : ""),
    );
    const viewer = page.getByTestId("gstile-viewer");
    await expect(viewer).toHaveAttribute("data-status", "Prêt", {
      timeout: 90_000,
    });
    const resident = Number(await viewer.getAttribute("data-resident-gaussians"));
    const selected = Number(await viewer.getAttribute("data-selected-nodes"));
    expect(resident).toBeGreaterThan(0);
    expect(resident).toBeLessThanOrEqual(7_500_000);
    expect(selected).toBeGreaterThan(0);
    expect(rangeRequestCount()).toBeGreaterThan(0);
    console.log(
      JSON.stringify({
        event: "gstile_initial_render",
        adapterInfo,
        residentGaussians: resident,
        selectedNodes: selected,
        rangeRequests: rangeRequestCount(),
      }),
    );

    const canvas = viewer.locator("canvas");
    const canvasBox = await canvas.boundingBox();
    expect(canvasBox).not.toBeNull();
    // HUD updates must not make a blank canvas pass the visual smoke check.
    await expect.poll(async () => {
      return page.evaluate(async (png) => {
        const blob = await (await fetch("data:image/png;base64," + png)).blob();
        const bitmap = await createImageBitmap(blob);
        const surface = new OffscreenCanvas(bitmap.width, bitmap.height);
        const context = surface.getContext("2d");
        if (!context) throw new Error("Screenshot inspection needs a 2D context");
        context.drawImage(bitmap, 0, 0);
        const { data } = context.getImageData(16, 160, bitmap.width - 32, bitmap.height - 176);
        bitmap.close();
        const minimum = [255, 255, 255];
        const maximum = [0, 0, 0];
        for (let index = 0; index < data.length; index += 4) {
          for (let channel = 0; channel < 3; channel += 1) {
            minimum[channel] = Math.min(minimum[channel], data[index + channel]);
            maximum[channel] = Math.max(maximum[channel], data[index + channel]);
          }
        }
        return Math.max(...maximum.map((value, channel) => value - minimum[channel]));
      }, (await canvas.screenshot()).toString("base64"));
    }, { timeout: 10_000, message: "GSTile must render visible scene pixels below the HUD" }).toBeGreaterThan(8);
    const beforePan = await canvas.screenshot({ path: testInfo.outputPath("initial-v4-gstile.png") });
    await page.keyboard.down("Shift");
    await page.mouse.move(
      canvasBox!.x + canvasBox!.width / 2,
      canvasBox!.y + canvasBox!.height / 2,
    );
    await page.mouse.down();
    await page.mouse.move(
      canvasBox!.x + canvasBox!.width / 2 + 140,
      canvasBox!.y + canvasBox!.height / 2 + 60,
      { steps: 6 },
    );
    await page.mouse.up();
    await page.keyboard.up("Shift");
    await page.waitForTimeout(300);
    expect((await canvas.screenshot()).equals(beforePan)).toBe(false);
    await expect(viewer).toHaveAttribute("data-status", "Prêt");
    await expect(viewer).not.toHaveAttribute("data-lod-state", "refining", {
      timeout: 90_000,
    });
    await expect(viewer).toHaveAttribute("data-pending-nodes", "0");

    await canvas.hover();
    await page.mouse.wheel(0, -2_400);
    await expect(viewer).toHaveAttribute("data-status", "Prêt");
    await expect(viewer).toHaveAttribute("data-pending-nodes", "0", { timeout: 90_000 });
    await expect(viewer).not.toHaveAttribute("data-lod-state", "refining");
    const residentAfterZoom = Number(
      await viewer.getAttribute("data-resident-gaussians"),
    );
    const targetAfterZoom = Number(
      await viewer.getAttribute("data-target-gaussians"),
    );
    expect(residentAfterZoom).toBe(targetAfterZoom);
    expect(Number(await viewer.getAttribute("data-selected-nodes"))).toBe(
      Number(await viewer.getAttribute("data-target-nodes")),
    );
    expect(residentAfterZoom).toBeLessThanOrEqual(7_500_000);
    console.log(
      JSON.stringify({
        event: "gstile_zoom_reselection",
        rangeRequests: rangeRequestCount(),
        residentGaussians: Number(
          await viewer.getAttribute("data-resident-gaussians"),
        ),
        selectedNodes: Number(await viewer.getAttribute("data-selected-nodes")),
      }),
    );
    await page.screenshot({
      path: testInfo.outputPath("current-v4-gstile.png"),
      fullPage: true,
    });
  } finally {
    await closeServer(server);
  }
});

}

test("refuses a retired manifest profile before requesting packs", async ({
  page,
}) => {
  const bundleRoot = process.env.GSTILE_BUNDLE_ROOT;
  test.skip(
    !bundleRoot,
    "GSTILE_BUNDLE_ROOT is required for retired-profile rejection",
  );
  test.setTimeout(2 * 60_000);
  const manifest = { ...loadManifest(bundleRoot!), profile: "dronegs-sh3-opacity-sh3-q96" };
  let packRequestCount = 0;
  await mockMissionApi(
    page,
    descriptor(manifest, (pack) => `http://127.0.0.1:9/${pack.path}`),
  );
  await page.route("http://127.0.0.1:9/**", async (route) => {
    packRequestCount += 1;
    await route.abort();
  });
  await page.goto("/missions/gstile-qualification");
  const viewer = page.getByTestId("gstile-viewer");
  await expect(viewer).toHaveAttribute("data-status", "Échec", {
    timeout: 90_000,
  });
  await expect(viewer).toContainText("profile");
  expect(packRequestCount).toBe(0);
});
