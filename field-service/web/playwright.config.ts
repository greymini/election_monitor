import { defineConfig } from "@playwright/test";

// Browser tests run against `python -m scripts.demo` (the API, the built app at /, sample data).
// Edge is used through its installed channel: nothing is downloaded.
const base = process.env.EM_BASE_URL ?? "http://127.0.0.1:8787";

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: base,
    channel: "msedge",
    headless: true,
    viewport: { width: 1600, height: 1000 },
    locale: "hi-IN",
    timezoneId: "Asia/Kolkata",
    trace: "retain-on-failure",
  },
});
