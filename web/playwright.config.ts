import { defineConfig, devices } from '@playwright/test'

/**
 * Playwright against the fixture build, so these need no database or API.
 *
 * `VITE_FIXTURES=1` is what makes that true: `lib/api.ts` answers every request
 * from `src/fixtures/`, which since this batch is 305 booths generated from
 * `fixtures/giridih.py` - the same source `tests/metric_cases.py` uses.
 *
 * Section 4 of the hardening brief wants these run against the real dev stack
 * instead; that is `scripts/dev_stack.py` and section 1, and it is a base-URL
 * change here when it exists.
 */
export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:5173',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: process.env.E2E_BASE_URL
    ? undefined
    : {
      command: 'npm run dev',
      url: 'http://localhost:5173',
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      env: { VITE_FIXTURES: '1' },
    },
})
