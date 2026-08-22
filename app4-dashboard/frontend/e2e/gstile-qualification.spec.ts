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
  source: { gaussianCount: number };
  packs: ManifestPack[];
};

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
  quality_profile: "high-quality-v1",
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
  });

test("streams the real hierarchical Saint-Etienne bundle without an incomplete cut", async ({
  page,
}, testInfo) => {
  const bundleRoot = process.env.GSTILE_BUNDLE_ROOT;
  test.skip(!bundleRoot, "GSTILE_BUNDLE_ROOT is required for real-bundle qualification");
  test.setTimeout(5 * 60_000);
  const manifest = loadManifest(bundleRoot!);
  const { server, origin, rangeRequestCount } = await startRangeServer(
    bundleRoot!,
    manifest,
  );
  try {
    await mockMissionApi(
      page,
      descriptor(manifest, (pack) => `${origin}/${pack.path}`),
    );
    await page.goto("/missions/gstile-qualification");
    const viewer = page.getByTestId("gstile-viewer");
    await expect(viewer).toHaveAttribute("data-status", "Prêt", {
      timeout: 4 * 60_000,
    });
    const resident = Number(await viewer.getAttribute("data-resident-gaussians"));
    const selected = Number(await viewer.getAttribute("data-selected-nodes"));
    expect(resident).toBeGreaterThan(0);
    expect(resident).toBeLessThanOrEqual(2_000_000);
    expect(selected).toBeGreaterThan(0);
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
          }
        : null;
    });
    expect(adapterInfo).not.toBeNull();
    console.log(
      JSON.stringify({
        event: "gstile_initial_render",
        adapterInfo,
        residentGaussians: resident,
        selectedNodes: selected,
        rangeRequests: rangeRequestCount(),
      }),
    );

    const requestsBeforeZoom = rangeRequestCount();
    await viewer.locator("canvas").hover();
    await page.mouse.wheel(0, -2_400);
    await expect.poll(rangeRequestCount, { timeout: 60_000 }).toBeGreaterThan(
      requestsBeforeZoom,
    );
    await expect(viewer).toHaveAttribute("data-status", "Prêt");
    expect(
      Number(await viewer.getAttribute("data-resident-gaussians")),
    ).toBeLessThanOrEqual(2_000_000);
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
      path: testInfo.outputPath("saint-etienne-gstile-lod.png"),
      fullPage: true,
    });
  } finally {
    await closeServer(server);
  }
});

test("refuses the real 49-million-splat exact bundle before requesting packs", async ({
  page,
}) => {
  const bundleRoot = process.env.GSTILE_EXACT_BUNDLE_ROOT;
  test.skip(
    !bundleRoot,
    "GSTILE_EXACT_BUNDLE_ROOT is required for exact-profile safety qualification",
  );
  test.setTimeout(2 * 60_000);
  const manifest = loadManifest(bundleRoot!);
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
  await expect(viewer).toContainText("exigent le LOD hiérarchique");
  expect(packRequestCount).toBe(0);
  expect(manifest.source.gaussianCount).toBeGreaterThan(2_000_000);
});
