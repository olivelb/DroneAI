import { defineConfig, devices } from "@playwright/test";

const runGsTileQualification = Boolean(
  process.env.GSTILE_BUNDLE_ROOT,
);

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI
    ? [["line"], ["html", { open: "never" }]]
    : "line",
  use: {
    baseURL: "http://127.0.0.1:3000",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      testIgnore: /gstile-qualification\.spec\.ts/,
      use: {
        ...devices["Desktop Chrome"],
        ...(process.env.GSTILE_CHROME_EXECUTABLE
          ? {
              launchOptions: {
                executablePath: process.env.GSTILE_CHROME_EXECUTABLE,
                args: ["--disable-software-rasterizer", "--force_high_performance_gpu"],
              },
            }
          : {}),
      },
    },
    ...(runGsTileQualification
      ? [
          {
            name: "gstile-webgpu",
            testMatch: /gstile-qualification\.spec\.ts/,
            use: {
              ...devices["Desktop Chrome"],
              viewport: { width: 1600, height: 900 },
              launchOptions: {
                executablePath: process.env.GSTILE_CHROME_EXECUTABLE,
                args: [
                  "--disable-software-rasterizer",
                  ...(process.platform === "win32"
                    ? ["--force_high_performance_gpu"]
                    : ["--enable-features=Vulkan", "--use-angle=vulkan"]),
                ],
              },
            },
          },
        ]
      : []),
  ],
  webServer: process.env.GSTILE_EXTERNAL_SERVER === "1" ? undefined : {
    command: "corepack npm run start -- --hostname 127.0.0.1 --port 3000",
    url: "http://127.0.0.1:3000",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
