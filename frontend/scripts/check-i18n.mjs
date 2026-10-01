/**
 * Resolve every translation key through real i18next and report any that miss.
 *
 * Item 6 of the hardening brief: "add a test that fails on any missing i18n key
 * in either language (i18next missingKeyHandler in tests)".
 *
 * A static comparison of source against JSON is not enough, and this project
 * has the scar to prove it: `health.openReviews` was reported rendering as a
 * raw key. A static scan says it resolves, and it does - but only because
 * i18next selects `openReviews_other` from a call carrying `count`. Get the
 * plural suffixes wrong, or pass `{{n}}` where i18next wants `count`, and the
 * key falls through to its own name on the page while every file-level check
 * still passes. Only i18next knows what i18next will do, so this asks it.
 *
 * Both languages, every key in either bundle, and the dynamic key families the
 * source builds by template - `nav.${key}`, `health.${state}`, `theme.${mode}` -
 * which a literal scan cannot see at all.
 *
 * It also scans the source for literal `t('section.key')` calls. Checking only
 * the keys the bundles define could never find a key the code uses and nobody
 * defined - `health.roll` rendered as the raw text "health.roll" on the
 * Overview of every AC without a roll, and the booth drawer's dialog was
 * labelled "card.heading", both with this check passing.
 *
 * Run: node scripts/check-i18n.mjs        (from frontend/)
 * Exit 0 clean, 1 with findings. tests/test_i18n_keys.py runs it.
 */

import { readdirSync, readFileSync, statSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

import i18next from 'i18next'

const here = dirname(fileURLToPath(import.meta.url))
const root = resolve(here, '..')

const bundles = {
  en: JSON.parse(readFileSync(resolve(root, 'src/i18n/en.json'), 'utf8')),
  hi: JSON.parse(readFileSync(resolve(root, 'src/i18n/hi.json'), 'utf8')),
}

/** Every leaf key path in a bundle. */
function flatten(obj, prefix = '') {
  const out = {}
  for (const [key, value] of Object.entries(obj)) {
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      Object.assign(out, flatten(value, `${prefix}${key}.`))
    } else {
      out[`${prefix}${key}`] = value
    }
  }
  return out
}

const flat = { en: flatten(bundles.en), hi: flatten(bundles.hi) }

const PLURAL_SUFFIX = /_(zero|one|two|few|many|other)$/

/** Base keys for the plural families, which is what call sites name. */
const pluralBases = new Set(
  Object.keys(flat.en).filter((k) => PLURAL_SUFFIX.test(k))
    .map((k) => k.replace(PLURAL_SUFFIX, '')),
)

/** Keys to ask for: every non-plural leaf, plus each plural family's base. */
const candidates = new Set()
for (const lang of ['en', 'hi']) {
  for (const key of Object.keys(flat[lang])) {
    candidates.add(PLURAL_SUFFIX.test(key) ? key.replace(PLURAL_SUFFIX, '') : key)
  }
}

/** Literal keys used in the source: t('a.b'), t("a.b"), i18nKey="a.b". */
function sourceFiles(dir) {
  const out = []
  for (const name of readdirSync(dir)) {
    const path = resolve(dir, name)
    if (statSync(path).isDirectory()) {
      if (name !== 'fixtures' && name !== 'test' && name !== '__tests__') out.push(...sourceFiles(path))
    } else if (/\.(ts|tsx)$/.test(name) && !/\.test\.tsx?$/.test(name)) {
      out.push(path)
    }
  }
  return out
}

const used = new Map()
const USE = /(?:\bt|i18n\.t)\(\s*['"]([a-z][\w]*(?:\.[\w]+)+)['"]|i18nKey=['"]([a-z][\w]*(?:\.[\w]+)+)['"]/g
for (const file of sourceFiles(resolve(root, 'src'))) {
  const text = readFileSync(file, 'utf8')
  for (const match of text.matchAll(USE)) {
    const key = match[1] ?? match[2]
    if (!used.has(key)) used.set(key, file.slice(root.length + 1))
  }
}
const defined = (lang, key) =>
  key in flat[lang] || Object.keys(flat[lang]).some((k) => k.replace(PLURAL_SUFFIX, '') === key)

const missing = []

await i18next.init({
  resources: {
    en: { translation: bundles.en },
    hi: { translation: bundles.hi },
  },
  lng: 'en',
  // No fallback. `fallbackLng: 'en'` is right for the running app - an English
  // string is better than a raw key - but it is exactly wrong here: it would
  // silently satisfy every Hindi lookup from the English bundle and this check
  // would pass with hi.json empty.
  fallbackLng: false,
  interpolation: { escapeValue: false },
  saveMissing: true,
  missingKeyHandler: (lngs, ns, key) => {
    missing.push({ lng: Array.isArray(lngs) ? lngs.join(',') : String(lngs), key })
  },
})

const problems = []

for (const lang of ['en', 'hi']) {
  await i18next.changeLanguage(lang)
  for (const key of [...candidates].sort()) {
    // `count` for the plural families, and a value for every placeholder the
    // string declares, so an unresolved interpolation is visible too.
    const options = pluralBases.has(key) ? { count: 2 } : {}
    const template = flat[lang][key] ?? flat[lang][`${key}_other`] ?? ''
    for (const name of String(template).matchAll(/\{\{\s*([A-Za-z_][\w]*)\s*\}\}/g)) {
      if (!(name[1] in options)) options[name[1]] = 'X'
    }

    const value = i18next.t(key, options)

    if (value === key) {
      problems.push(`${lang}: ${key} resolved to its own name`)
      continue
    }
    if (typeof value !== 'string' || value.trim() === '') {
      problems.push(`${lang}: ${key} resolved to an empty string`)
      continue
    }
    if (value.includes('{{')) {
      problems.push(`${lang}: ${key} still contains an unresolved {{placeholder}}: ${value}`)
    }
  }
}

for (const entry of missing) {
  problems.push(`${entry.lng}: ${entry.key} reported missing by i18next`)
}

// Both bundles must carry the same keys. Without `fallbackLng` above, a key
// present only in English would already have failed for Hindi - this reports it
// as the shape problem it is rather than as 40 separate lookups.
for (const [key, file] of [...used].sort()) {
  for (const lang of ['en', 'hi']) {
    if (!defined(lang, key)) problems.push(`${lang}: ${key} is used in ${file} but not defined`)
  }
}

const onlyEn = Object.keys(flat.en).filter((k) => !(k in flat.hi))
const onlyHi = Object.keys(flat.hi).filter((k) => !(k in flat.en))
for (const key of onlyEn) problems.push(`missing from hi.json: ${key}`)
for (const key of onlyHi) problems.push(`missing from en.json: ${key}`)

if (problems.length) {
  console.error(`${problems.length} i18n problem(s):`)
  for (const problem of problems) console.error(`  ${problem}`)
  process.exit(1)
}

console.log(
  `i18n ok: ${candidates.size} keys resolve in both languages `
  + `(${pluralBases.size} plural families); ${used.size} keys used in the source are all defined`,
)
