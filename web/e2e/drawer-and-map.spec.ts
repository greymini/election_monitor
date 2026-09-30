import { expect, test } from '@playwright/test'

/**
 * Browser checks for the drawer and map defects reported from the screens.
 *
 * **These have never been executed.** Playwright's package installed here but
 * the Chromium download failed - `Failed to download Chrome for Testing
 * 153.0.8010.12`, twice - so there is no browser on this machine. They are
 * written because items 1 and 3 of the fix list ask for e2e checks and because
 * the assertions are the specification of what "fixed" means; they are recorded
 * as NOT RUN in UAT_READINESS.md until an operator with a browser runs them.
 *
 *   cd web && npx playwright install chromium && npx playwright test
 *
 * Fixture mode, so no database or API is needed: `VITE_FIXTURES=1 npm run dev`.
 * The 305-booth fixture is what makes these meaningful - the previous eight
 * booths could not produce the overflow or the marker density being tested.
 */

const AC = '/acs/32'

/** Widths the brief names for the layout checks. */
const WIDTHS = [1440, 1024, 390]

async function openFirstBooth(page: import('@playwright/test').Page) {
  await page.goto(`${AC}/map`)
  // Markers are SVG circles drawn by Leaflet, not DOM nodes with test ids.
  const marker = page.locator('.leaflet-interactive').first()
  await expect(marker).toBeVisible()
  await marker.click()
  await expect(page.getByTestId('booth-drawer')).toBeVisible()
}

test.describe('booth drawer over the map', () => {
  // Item 1. Leaflet assigns z-indexes up to 1000 to its own panes and control
  // corners, and those were siblings of the drawer at z-40 in the page's root
  // stacking context - so the drawer rendered underneath the map it was opened
  // from. The fix is `isolation: isolate` on the map wrapper plus z-[1200] on
  // the drawer; this is the check that it worked.
  test('the drawer is fully visible above the Leaflet panes', async ({ page }) => {
    await openFirstBooth(page)
    const panel = page.getByTestId('booth-drawer-panel')
    await expect(panel).toBeVisible()

    const box = await panel.boundingBox()
    expect(box).not.toBeNull()
    if (!box) return

    // Every corner of the panel must hit the panel, not a map pane. This is
    // the assertion that actually distinguishes "on top" from "behind":
    // toBeVisible passes for an element a map is covering.
    const probes = [
      { x: box.x + 4, y: box.y + 4 },
      { x: box.x + box.width - 4, y: box.y + 4 },
      { x: box.x + 4, y: box.y + box.height - 4 },
      { x: box.x + box.width - 4, y: box.y + box.height - 4 },
      { x: box.x + box.width / 2, y: box.y + box.height / 2 },
    ]
    for (const point of probes) {
      const onTop = await page.evaluate(
        ({ x, y }) => {
          const el = document.elementFromPoint(x, y)
          return !!el?.closest('[data-testid="booth-drawer"]')
        },
        point,
      )
      expect(
        onTop,
        `point (${Math.round(point.x)}, ${Math.round(point.y)}) is covered by `
        + 'something other than the drawer - most likely a Leaflet pane',
      ).toBe(true)
    }
  })

  test('escape closes it', async ({ page }) => {
    await openFirstBooth(page)
    await page.keyboard.press('Escape')
    await expect(page.getByTestId('booth-drawer')).toHaveCount(0)
  })
})

test.describe('no horizontal page overflow', () => {
  // Item 2. Opening the drawer produced a horizontal page scrollbar, and with
  // the page scrolled right the sticky header looked clipped. The drawer is
  // portalled to <body> and locks overflow on <html> while open.
  for (const width of WIDTHS) {
    for (const drawer of ['closed', 'open'] as const) {
      test(`${width}px, drawer ${drawer}`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 })
        if (drawer === 'open') {
          await openFirstBooth(page)
        } else {
          await page.goto(`${AC}/map`)
          await expect(page.locator('.leaflet-interactive').first()).toBeVisible()
        }

        const overflow = await page.evaluate(() => {
          const root = document.documentElement
          return {
            scrollWidth: root.scrollWidth,
            clientWidth: root.clientWidth,
            // The widest element that sticks out, to name the culprit rather
            // than just reporting a number.
            culprit: (() => {
              let worst: { tag: string; right: number } | null = null
              for (const el of Array.from(document.body.querySelectorAll('*'))) {
                const rect = el.getBoundingClientRect()
                if (rect.width === 0) continue
                if (rect.right > root.clientWidth + 1) {
                  if (!worst || rect.right > worst.right) {
                    worst = {
                      tag: `${el.tagName.toLowerCase()}.${(el.className || '')
                        .toString().split(' ').slice(0, 3).join('.')}`,
                      right: Math.round(rect.right),
                    }
                  }
                }
              }
              return worst
            })(),
          }
        })

        expect(
          overflow.scrollWidth,
          `page scrolls horizontally: scrollWidth ${overflow.scrollWidth} > `
          + `clientWidth ${overflow.clientWidth}`
          + (overflow.culprit
            ? `; widest overhang is ${overflow.culprit.tag} reaching `
              + `${overflow.culprit.right}px`
            : ''),
        ).toBeLessThanOrEqual(overflow.clientWidth + 1)
      })
    }
  }

  test('the header is not clipped with the drawer open', async ({ page }) => {
    await page.setViewportSize({ width: 1024, height: 900 })
    await page.goto(`${AC}/map`)
    const header = page.locator('header').first()
    const before = await header.boundingBox()
    await openFirstBooth(page)
    const after = await header.boundingBox()
    expect(before).not.toBeNull()
    expect(after).not.toBeNull()
    if (!before || !after) return
    // The header must keep its width when the drawer opens. It lost it to the
    // scrollbar gutter before, which is what made it look cut off.
    expect(Math.abs(after.width - before.width)).toBeLessThanOrEqual(1)
  })
})

test.describe('map markers and tooltip', () => {
  // Item 4. Every booth marker must be a circle. A dark square with truncated
  // text was reported; no <Marker> or divIcon exists in the source, so this
  // checks the rendered result rather than the code.
  test('every marker is a circle', async ({ page }) => {
    await page.goto(`${AC}/map`)
    await expect(page.locator('.leaflet-interactive').first()).toBeVisible()

    const shapes = await page.evaluate(() =>
      Array.from(document.querySelectorAll('.leaflet-overlay-pane svg *'))
        .map((el) => el.tagName.toLowerCase())
        .filter((tag) => tag !== 'g' && tag !== 'svg'),
    )
    expect(shapes.length).toBeGreaterThan(0)
    expect([...new Set(shapes)]).toEqual(['path'])

    // Leaflet draws a CircleMarker as a <path> with an arc command. A square
    // would be straight segments only, which is the shape reported.
    const withArcs = await page.evaluate(() =>
      Array.from(document.querySelectorAll('.leaflet-overlay-pane svg path'))
        .every((el) => (el.getAttribute('d') || '').toLowerCase().includes('a')),
    )
    expect(withArcs, 'a marker path has no arc command, so it is not a circle').toBe(true)

    // No marker-shaped DOM icons at all: those are what render as a box with
    // alt text when the icon image 404s under a bundler.
    await expect(page.locator('.leaflet-marker-icon')).toHaveCount(0)
  })

  // Item 3. The tooltip and the booth card must agree. The fixture now stores
  // each figure once, computed by analytics.metrics, so this is the check that
  // both screens read it.
  test('the tooltip and the booth card agree on margin and electors', async ({ page }) => {
    await page.goto(`${AC}/map`)
    const marker = page.locator('.leaflet-interactive').first()
    await expect(marker).toBeVisible()

    await marker.hover()
    const tooltip = page.locator('.leaflet-tooltip')
    await expect(tooltip).toBeVisible()
    const tooltipText = (await tooltip.innerText()).replace(/\s+/g, ' ')

    await marker.click()
    await expect(page.getByTestId('booth-drawer')).toBeVisible()
    const cardText = (await page.getByTestId('booth-drawer-panel').innerText())
      .replace(/\s+/g, ' ')

    // The electorate the tooltip shows must appear on the card. Comparing the
    // rendered strings is the point: the reported defect was a tooltip with
    // numbers over a card showing dashes.
    const electors = tooltipText.match(/([\d,]{3,})/g) ?? []
    expect(electors.length, `no numbers in the tooltip: ${tooltipText}`).toBeGreaterThan(0)
    for (const value of electors) {
      expect(
        cardText,
        `the tooltip shows ${value} but the booth card does not: ${cardText.slice(0, 300)}`,
      ).toContain(value)
    }
    expect(cardText).not.toMatch(/—\s*—/)
  })

  test('the legend names both parties and the sign', async ({ page }) => {
    await page.goto(`${AC}/map`)
    const legend = page.locator('.card', { hasText: 'JMM' }).first()
    await expect(legend).toBeVisible()
    const text = await legend.innerText()
    // Item 5: both ends read "+30" with no party before this.
    expect(text).toMatch(/JMM\s*\+\d+%/)
    expect(text).toMatch(/BJP\s*\+\d+%/)
  })
})
