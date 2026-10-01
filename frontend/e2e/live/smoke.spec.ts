import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { expect, test, type Page } from '@playwright/test'

/**
 * Live smoke test: the real UI against the real API on `make dev-stack`.
 *
 * Every page each role can reach, for AC 32 (loaded) and AC 42 (seeded,
 * nothing loaded). A page fails on a console error, a failed /api request
 * (a 403 the role is meant to get is the only exception), or visible
 * "{{", "undefined", "NaN" or "[object Object]".
 *
 * Logins come from backend/.devstack/users.json, which dev_stack.py writes.
 */
interface DevUser { phone: string; password: string; role: 'admin' | 'strategist' | 'block' }
const USERS: DevUser[] = JSON.parse(
  readFileSync(resolve(dirname(fileURLToPath(import.meta.url)),
    '../../../backend/.devstack/users.json'), 'utf8'))
const user = (role: DevUser['role']) => USERS.find((u) => u.role === role)!

const COMMON = ['/', '/results', '/booths', '/map', '/voters', '/candidates', '/local-politics',
  '/local', '/news', '/factors', '/compare']
const PAGES: Record<DevUser['role'], string[]> = {
  admin: [...COMMON, '/caste', '/caste-scatter', '/transfer', '/scenario', '/admin'],
  strategist: [...COMMON, '/caste', '/caste-scatter', '/transfer', '/scenario'],
  block: COMMON,
}
const BAD_TEXT = /\{\{|\bundefined\b|\bNaN\b|\[object Object\]/

async function signIn(page: Page, role: DevUser['role']) {
  await page.addInitScript(() => localStorage.setItem('giridih.lang', 'en'))
  await page.goto('/')
  const u = user(role)
  await page.getByLabel('Phone number').fill(u.phone)
  await page.getByLabel('Password').fill(u.password)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('button', { name: 'Sign out' }).first()).toBeVisible()
}

function watch(page: Page) {
  const problems: string[] = []
  page.on('console', (msg) => {
    if (msg.type() === 'error') problems.push(`console: ${msg.text()}`)
  })
  page.on('pageerror', (err) => problems.push(`page error: ${err.message}`))
  page.on('response', (res) => {
    const url = res.url()
    if (url.includes('/api/') && res.status() >= 400 && res.status() !== 403) {
      problems.push(`${res.status()} ${url}`)
    }
  })
  return problems
}

async function settle(page: Page) {
  await page.waitForLoadState('networkidle')
  await expect(page.getByText(/^Loading…$/)).toHaveCount(0, { timeout: 15_000 })
}

for (const role of ['admin', 'strategist', 'block'] as const) {
  test(`${role}: every page loads cleanly for AC 32 and an empty AC`, async ({ page }) => {
    test.setTimeout(180_000)
    const problems = watch(page)
    await signIn(page, role)
    const acs = role === 'block' ? [32] : [32, 42]
    for (const ac of acs) {
      for (const path of PAGES[role]) {
        await page.goto(`${path}?ac=${ac}`)
        await settle(page)
        const text = await page.locator('main').innerText()
        expect(text, `${role} ${path}?ac=${ac}`).not.toMatch(BAD_TEXT)
        expect(text, `${role} ${path}?ac=${ac} crashed`).not.toMatch(/something went wrong/i)
      }
    }
    expect(problems).toEqual([])
  })
}

test('block user cannot reach strategist pages by typing the URL', async ({ page }) => {
  await signIn(page, 'block')
  for (const path of ['/caste', '/transfer', '/scenario', '/admin']) {
    await page.goto(`${path}?ac=32`)
    await settle(page)
    await expect(page).toHaveURL(/\/\?ac=32$|\/$/)
  }
})

test('booth drawer: every tab, from the map, with boundaries toggled', async ({ page }) => {
  const problems = watch(page)
  await signIn(page, 'admin')
  await page.goto('/map?ac=32')
  await settle(page)
  const toggle = page.getByRole('button', { name: /Boundaries (on|off)/ })
  await toggle.click()
  await toggle.click()
  const marker = page.locator('.leaflet-overlay-pane path.leaflet-interactive').last()
  await marker.click()
  const drawer = page.getByRole('dialog')
  await expect(drawer).toBeVisible()
  for (const tab of ['Results', 'Voters', 'Community', 'News', 'Ground', 'Sources']) {
    await drawer.getByRole('button', { name: tab, exact: true }).click()
    await expect(drawer).not.toContainText(/something went wrong/i)
    expect(await drawer.innerText()).not.toMatch(BAD_TEXT)
  }
  expect(problems).toEqual([])
})

test('scenario projects a margin', async ({ page }) => {
  await signIn(page, 'strategist')
  await page.goto('/scenario?ac=32')
  await page.getByRole('button', { name: 'Project' }).click()
  await expect(page.getByText('Projected margin')).toBeVisible()
  expect(await page.locator('main').innerText()).not.toMatch(BAD_TEXT)
})

test('booth table exports a CSV', async ({ page }) => {
  await signIn(page, 'admin')
  await page.goto('/booths?ac=32')
  await settle(page)
  const [download] = await Promise.all([
    page.waitForEvent('download'),
    page.getByRole('button', { name: /export csv/i }).click(),
  ])
  const path = await download.path()
  const csv = readFileSync(path!, 'utf8')
  expect(csv.split('\n')[0]).toContain('booth_uid')
  expect(csv.split('\n').length).toBeGreaterThan(50)
})

test('admin resolves a review item', async ({ page }) => {
  await signIn(page, 'admin')
  await page.goto('/admin?ac=32')
  await settle(page)
  await page.getByRole('button', { name: 'review', exact: true }).click()
  const resolveButtons = page.getByRole('button', { name: 'Resolved' })
  // Wait for the list (or its empty state) before counting.
  await expect(resolveButtons.first().or(page.getByText('Nothing is waiting for review.')))
    .toBeVisible()
  const before = await resolveButtons.count()
  test.skip(before === 0, 'no open review items in the dev dataset')
  // The list is capped at 100 of 227 open items, so the count does not drop:
  // the resolved item is what must disappear.
  const firstCard = page.locator('div.card', { has: resolveButtons.first() }).first()
  const ref = (await firstCard.locator('.font-mono').first().innerText()).trim()
  await resolveButtons.first().click()
  await expect(page.locator('.font-mono', { hasText: ref })).toHaveCount(0)
})
