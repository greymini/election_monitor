import { BOOTH_COUNT, GENERATED_BOOTHS } from '../src/fixtures/generated'
import { expect, gotoReady, test } from './fixtures'

/**
 * The Overview, against the 305-booth fixture.
 *
 * The expected counts are **imported from the generated fixture**, not written
 * out. That is the whole point of the first test: the health cards went on
 * reporting 8 booths, 9 PS rows and 7-of-8 placed after the fixture was
 * replaced, because those numbers were literals in `responses.ts` rather than
 * counts of anything. A test with its own hardcoded 305 would rot the same way
 * the moment the fixture changed again.
 */

const UNGEOCODED = GENERATED_BOOTHS.filter((b) => b.lat === null).length
const WEAK_CROSSWALK = GENERATED_BOOTHS.filter((b) => !b.crosswalk_reviewed).length

test.describe('data comes from the single fixture source', () => {
  test('the admin data screen counts match the source', async ({ page }) => {
    await gotoReady(page, '/admin')
    const strip = page.getByTestId('data-health')
    await expect(strip).toBeVisible()
    const text = (await strip.innerText()).replace(/\s+/g, ' ')

    // Booth-derived counts. Written with the grouping the UI uses.
    const grouped = BOOTH_COUNT.toLocaleString('en-IN')
    expect(text, 'the polling-station count is not the source count')
      .toContain(`${grouped} rows`)
    expect(text, 'the placed-booth count is not the source count')
      .toContain(`${(BOOTH_COUNT - UNGEOCODED).toLocaleString('en-IN')} of ${grouped}`)
    expect(text, 'the weak-crosswalk count is not the source count')
      .toContain(`${WEAK_CROSSWALK}`)

    // The old literals, asserted absent. This is the regression that was
    // reported: the numbers below are what the cards showed after the fixture
    // was replaced.
    expect(text).not.toContain('9 rows')
    expect(text).not.toContain('7 of 8')
    expect(text).not.toContain('8 rows')
  })

  test('the booth table row count matches the source', async ({ page }) => {
    await gotoReady(page, '/booths')
    // Whatever the table paginates to, its reported total is the source total.
    const body = (await page.locator('main').innerText()).replace(/\s+/g, ' ')
    expect(body).toContain(BOOTH_COUNT.toLocaleString('en-IN'))
  })
})

test.describe('the Overview carries no data operations', () => {
  test('no shell command appears anywhere on it', async ({ page }) => {
    await gotoReady(page, '/')
    await expect(page.getByTestId('data-status')).toBeVisible()
    const body = await page.locator('main').innerText()

    // Item 2. A strategist does not need `python -m ingest.geocode`, and a
    // block in-charge has no shell to run it in.
    expect(body, 'a CLI command is still on the Overview').not.toContain('python -m')
    expect(body).not.toContain('--ac')
    // And the "count of zero" operator note went with it.
    expect(body).not.toContain('A count of zero')
  })

  test('the status line names what is missing, and links to admin', async ({ page }) => {
    await gotoReady(page, '/')
    const status = page.getByTestId('data-status')
    await expect(status).toBeVisible()
    const text = await status.innerText()

    expect(text).toContain('Form 20 loaded')
    // Census is zero in the fixture, so it must be named rather than summarised.
    expect(text).toContain('Census')
    // The fixture session is an admin, so the link is offered.
    await expect(status.getByRole('link', { name: /manage data sources/i })).toBeVisible()
  })

  test('the full strip is on admin instead', async ({ page }) => {
    await gotoReady(page, '/admin')
    await expect(page.getByTestId('data-health')).toBeVisible()
    const body = await page.locator('main').innerText()
    expect(body).toContain('python -m')
  })
})

test.describe('the margin card', () => {
  test('reads winner, margin, share and election without wrapping badly', async ({ page }) => {
    await gotoReady(page, '/')
    const card = page.locator('.card', { hasText: 'MARGIN' }).first()
    await expect(card).toBeVisible()
    const text = (await card.innerText()).replace(/\s+/g, ' ')

    // Item 3: headline names the winner and the margin.
    expect(text).toMatch(/JMM \+3,838/)
    // Subline: the share, the runner-up, the election.
    expect(text).toMatch(/1\.85% over BJP/)
    expect(text).toContain('VS-2024')
    // "fixture" is a badge from SourceLink, not loose sentence text.
    expect(text).not.toMatch(/over BJP · VS-2024fixture/)
  })

  for (const width of [1440, 1024, 390]) {
    test(`does not overflow its card at ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 })
      await gotoReady(page, '/')
      const card = page.locator('.card', { hasText: 'MARGIN' }).first()
      await expect(card).toBeVisible()

      const overflowing = await card.evaluate((el) => {
        const bounds = el.getBoundingClientRect()
        for (const child of Array.from(el.querySelectorAll('*'))) {
          const rect = child.getBoundingClientRect()
          if (rect.width > 0 && rect.right > bounds.right + 1) {
            return `${child.tagName.toLowerCase()}: ${child.textContent?.slice(0, 60)}`
          }
        }
        return null
      })
      expect(overflowing, `content overflows the margin card at ${width}px`).toBeNull()
    })
  }
})

test.describe('chart captions name the real source', () => {
  test('fixture mode says fixture, not Form 20', async ({ page }) => {
    await gotoReady(page, '/')
    const body = (await page.locator('main').innerText()).replace(/\s+/g, ' ')

    // Item 4. This said "From loaded Form 20 data" over invented data, on the
    // same page as a banner explaining that nothing here came from a document.
    expect(body, 'a chart still claims the data came from Form 20')
      .not.toMatch(/From loaded Form 20/i)
    expect(body).toMatch(/Fixture data/i)
  })
})

test.describe('the Overview is analytical', () => {
  // Item 5, each section with the page it links to.
  const sections: Array<[RegExp, string]> = [
    [/highest-priority booths/i, '/booths'],
    [/largest swings/i, '/results'],
    [/new-voter hotspots/i, '/voters'],
    [/community snapshot/i, '/caste'],
    [/latest news/i, '/news'],
  ]

  for (const [heading, href] of sections) {
    test(`${heading.source} is present and links to ${href}`, async ({ page }) => {
      await gotoReady(page, '/')
      const panel = page.locator('section.card').filter({ hasText: heading }).first()
      await expect(panel).toBeVisible()
      await expect(panel.locator(`a[href="${href}"]`)).toBeVisible()
    })
  }

  test('the priority list is ranked and no row is blank', async ({ page }) => {
    await gotoReady(page, '/')
    const panel = page.locator('section.card')
      .filter({ hasText: /highest-priority booths/i }).first()
    await expect(panel).toBeVisible()
    const items = panel.locator('li')
    // Wait for the list: counting before the panel's query resolved read 0
    // about one run in three.
    await expect(items.first()).toBeVisible()
    const count = await items.count()
    expect(count).toBeGreaterThan(0)
    expect(count).toBeLessThanOrEqual(10)

    for (let i = 0; i < count; i += 1) {
      const row = (await items.nth(i).innerText()).replace(/\s+/g, ' ')
      expect(row, `row ${i + 1} has no booth id`).toMatch(/32-B\d{4}/)
      expect(row, `row ${i + 1} shows no value`).toMatch(/\d/)
      expect(row).not.toContain('undefined')
      expect(row).not.toContain('NaN')
    }
  })
})

test.describe('no rendered page shows a raw key or a placeholder', () => {
  // The rendered half of section 3 item 1 and item 6, which a static scan
  // cannot do: `{{count}}` rendered literally beside a number on this page.
  const ROUTES = ['/', '/booths', '/results', '/voters', '/factors', '/news', '/admin']

  for (const route of ROUTES) {
    for (const lang of ['en', 'hi']) {
      test(`${route} in ${lang}`, async ({ page }) => {
        await page.addInitScript((value) => {
          window.localStorage.setItem('giridih.lang', value as string)
        }, lang)
        await page.goto(route)
        await expect(page.locator('main')).toBeVisible()
        const body = await page.locator('main').innerText()

        expect(body, 'an unresolved interpolation is on screen').not.toContain('{{')
        expect(body).not.toContain('undefined')
        expect(body).not.toContain('NaN')
        expect(body).not.toContain('[object Object]')
        expect(body).not.toContain('Invalid Date')
        // A raw i18n key looks like `section.camelCaseKey`. Matching that shape
        // rather than a list of keys, so a new one is caught too.
        const rawKey = body.match(/\b[a-z]+\.[a-z][a-zA-Z]{3,}\b(?!\.)/)
        expect(rawKey?.[0] ?? null, 'what looks like a raw i18n key is rendered')
          .toBeNull()
      })
    }
  }
})
