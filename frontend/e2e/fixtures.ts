import { test as base } from '@playwright/test'

/**
 * A signed-in page.
 *
 * Every spec failed on its first run because none of them logged in: the app
 * redirects to /login without a session, so the assertions were looking for a
 * map on a login form. In fixture mode any token is accepted - `lib/api.ts`
 * answers from `src/fixtures/` and never checks it - so seeding localStorage is
 * enough and avoids driving the login form in every test.
 *
 * `addInitScript` runs before the app's own scripts on every navigation, which
 * is what makes this work: setting localStorage after `goto` would be too late,
 * the app having already decided it was unauthenticated and redirected.
 */
export const test = base.extend({
  page: async ({ page }, use) => {
    await page.addInitScript(() => {
      window.localStorage.setItem('giridih.token', 'fixture-token')
      // Hindi is the default; the specs assert on English strings, so they pin
      // the language rather than depending on whatever the last run left.
      window.localStorage.setItem('giridih.lang', 'en')
    })
    await use(page)
  },
})

export { expect } from '@playwright/test'

/**
 * Navigate and wait for the page to finish loading.
 *
 * The Overview now issues four queries, and the fixture layer adds a
 * deliberate 60ms to each so loading states are reachable in review. Four
 * specs failed reading `main` while it still said "Loading…" - a race, not a
 * defect, and exactly the kind of flake that gets a suite ignored.
 *
 * Waits for the loading text to clear rather than for a timeout, so it is as
 * fast as the page and does not silently pass a page that never loads.
 */
export async function gotoReady(
  page: import('@playwright/test').Page,
  path: string,
) {
  await page.goto(path)
  const main = page.locator('main')
  await main.waitFor({ state: 'visible' })
  await expectFrom(main).not.toContainText('Loading', { timeout: 15_000 })
}

// Imported separately so the helper above can use it without a circular
// reference through the re-export below.
import { expect as expectFrom } from '@playwright/test'
