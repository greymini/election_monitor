import { defineConfig, devices } from '@playwright/test'

/**
 * Two projects.
 *
 *  fixtures  e2e/*.spec.ts against a dev server with VITE_FIXTURES=1 on :5173.
 *            No database or API: lib/api.ts answers from src/fixtures.
 *  live      e2e/live/*.spec.ts against a dev server *without* fixtures on
 *            :5174, which proxies /api to the API on :8000. Start the backend
 *            first: `make dev-stack` (backend/scripts/dev_stack.py), which
 *            also writes the per-role logins the live specs read.
 *
 * Browser: Playwright's Chromium by default (`npx playwright install
 * chromium`). Where that download is blocked, set PLAYWRIGHT_CHANNEL=msedge
 * (or chrome) to drive an installed browser instead - same engine.
 */
const channel = process.env.PLAYWRIGHT_CHANNEL || undefined
const browser = { ...devices['Desktop Chrome'], ...(channel ? { channel } : {}) }

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'fixtures',
      testIgnore: ['live/**'],
      use: { ...browser, baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:5173' },
    },
    {
      name: 'live',
      testMatch: ['live/**/*.spec.ts'],
      // One worker: the live specs share one database and some of them write.
      fullyParallel: false,
      use: { ...browser, baseURL: process.env.E2E_LIVE_URL ?? 'http://localhost:5174' },
    },
  ],
  webServer: [
    ...(process.env.E2E_BASE_URL ? [] : [{
      command: 'npm run dev',
      url: 'http://localhost:5173',
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      env: { VITE_FIXTURES: '1' },
    }]),
    ...(process.env.E2E_LIVE_URL ? [] : [{
      command: 'npm run dev -- --port 5174',
      url: 'http://localhost:5174',
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      env: { VITE_FIXTURES: '0' },
    }]),
  ],
})
