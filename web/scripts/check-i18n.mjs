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
 * Run: node scripts/check-i18n.mjs        (from web/)
 * Exit 0 clean, 1 with findings. tests/test_i18n_keys.py runs it.
 */

import { readFileSync } from 'node:fs'
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
  + `(${pluralBases.size} plural families)`,
)
