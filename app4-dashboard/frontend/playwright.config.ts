import { defineConfig, devices } from "@playwright/test";

const runGsTileQualification = Boolean(
  process.env.GSTILE_BUNDLE_ROOT || process.env.GSTILE_EXACT_BUNDLE_ROOT,
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
      use: { ...devices["Desktop Chrome"] },
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
                args: [
                  "--enable-unsafe-webgpu",
                  "--enable-features=Vulkan",
                  "--use-angle=vulkan",
                ],
              },
            },
          },
        ]
      : []),
  ],
  webServer: {
    command: "corepack npm run start -- --hostname 127.0.0.1 --port 3000",
    url: "http://127.0.0.1:3000",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
