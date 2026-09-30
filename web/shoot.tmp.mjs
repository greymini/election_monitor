// Drive the real dashboard in Edge, sign in, and capture each page.
import { chromium } from 'playwright'

const BASE = 'http://127.0.0.1:5173'
const OUT = process.argv[2]
const WIDE = { width: 1500, height: 1000 }

const PAGES = [
  ['overview',  '/',          null],
  ['map',       '/map',       '.leaflet-container'],
  ['results',   '/results',   'table'],
  ['voters',    '/voters',    null],
  ['caste',     '/caste',     null],
  ['transfer',  '/transfer',  'table'],
  ['news',      '/news',      null],
  ['scenario',  '/scenario',  null],
  ['factors',   '/factors',   null],
  ['local',     '/local',     'table'],
  ['admin',     '/admin',     null],
]

const browser = await chromium.launch({ channel: 'msedge', headless: true })
const ctx = await browser.newContext({ viewport: WIDE, deviceScaleFactor: 1.5, locale: 'hi-IN' })
const page = await ctx.newPage()

page.on('console', (m) => { if (m.type() === 'error') console.log('  console error:', m.text().slice(0, 160)) })
page.on('pageerror', (e) => console.log('  PAGE ERROR:', String(e).slice(0, 200)))

// Sign in (the demo API accepts anything)
await page.goto(BASE, { waitUntil: 'networkidle' })
await page.fill('input[autocomplete="username"]', '9999999999')
await page.fill('input[type="password"]', 'demo-password')
await page.click('button:has-text("साइन इन करें"), button:has-text("Sign in")')
await page.waitForSelector('header', { timeout: 20000 })
console.log('signed in')

for (const [name, route, waitFor] of PAGES) {
  await page.goto(BASE + route, { waitUntil: 'networkidle' })
  if (waitFor) await page.waitForSelector(waitFor, { timeout: 20000 }).catch(() => {})
  if (name === 'scenario') {
    await page.click('button:has-text("गणना करें"), button:has-text("Project")').catch(() => {})
    await page.waitForTimeout(2500)
  }
  await page.waitForTimeout(1400)   // let charts finish their entry animation
  await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: false })
  console.log('captured', name)
}

// Booth drawer, opened from the map
await page.goto(BASE + '/map', { waitUntil: 'networkidle' })
await page.waitForSelector('.leaflet-container', { timeout: 20000 })
await page.waitForTimeout(1500)
const marker = page.locator('path.leaflet-interactive').first()
await marker.click({ force: true }).catch(() => {})
await page.waitForTimeout(1800)
await page.screenshot({ path: `${OUT}/booth-card.png` })
console.log('captured booth-card')

// Chat panel
await page.goto(BASE + '/', { waitUntil: 'networkidle' })
await page.click('button:has-text("सहायक"), button:has-text("Assistant")')
await page.waitForTimeout(600)
await page.fill('textarea', '2024 में BJP ने किन बूथों पर सबसे ज़्यादा लीड ली?')
await page.click('button:has-text("भेजें"), button:has-text("Send")')
await page.waitForTimeout(3500)
await page.screenshot({ path: `${OUT}/chat.png` })
console.log('captured chat')

// English + dark mode, to prove both render
await page.click('button:has-text("English")').catch(() => {})
await page.waitForTimeout(500)
const themeBtn = page.locator('header button[aria-label^="Theme"]')
for (let i = 0; i < 3; i++) {
  const label = await themeBtn.getAttribute('aria-label')
  if (label && label.includes('dark')) break
  await themeBtn.click()
  await page.waitForTimeout(350)
}
await page.waitForTimeout(900)
await page.screenshot({ path: `${OUT}/overview-en-dark.png` })
console.log('captured overview-en-dark')

// Mobile width, which is what block in-charges actually use
const mobile = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, locale: 'hi-IN' })
const mp = await mobile.newPage()
await mp.goto(BASE, { waitUntil: 'networkidle' })
await mp.fill('input[autocomplete="username"]', '9999999999')
await mp.fill('input[type="password"]', 'demo-password')
await mp.click('button:has-text("साइन इन करें"), button:has-text("Sign in")')
await mp.waitForSelector('header', { timeout: 20000 })
await mp.goto(BASE + '/voters', { waitUntil: 'networkidle' })
await mp.waitForTimeout(2000)
await mp.screenshot({ path: `${OUT}/mobile-voters.png` })
console.log('captured mobile-voters')

await browser.close()
console.log('done')
