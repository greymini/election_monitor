/// <reference types="node" />
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

/**
 * Every CSS variable and custom utility the components use is defined.
 *
 * `--status-warn`, `--status-warn-bg`, `--status-ok`, the `.select` / `.input`
 * classes and the `text-3xs` size were all used and never defined, so badges,
 * form controls and 36 captions silently fell back to defaults.
 */
const SRC = resolve(__dirname, '../..')

function files(dir: string, ext: RegExp): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name)
    if (statSync(path).isDirectory()) return name === 'node_modules' ? [] : files(path, ext)
    return ext.test(name) && !/\.test\.tsx?$/.test(name) ? [path] : []
  })
}

const css = files(resolve(SRC, 'styles'), /\.css$/).map((f) => readFileSync(f, 'utf8')).join('\n')
// Block comments are stripped: prose such as "`var(--token)` does not resolve"
// is not a use of a variable.
const code = files(SRC, /\.(ts|tsx)$/)
  .map((f) => readFileSync(f, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '')).join('\n')
const tailwind = readFileSync(resolve(SRC, '../tailwind.config.js'), 'utf8')

describe('styles', () => {
  it('defines every CSS variable the components read', () => {
    const defined = new Set([...css.matchAll(/(--[\w-]+)\s*:/g)].map((m) => m[1]))
    // `--[\w-]*\w`: a templated name such as var(--status-${status}) is checked
    // separately below, not read as the literal "--status-".
    const used = new Set([...code.matchAll(/var\((--[\w-]+)(\$\{)?/g)]
      .filter((m) => !m[2]).map((m) => m[1]))
    const missing = [...used].filter((v) => !defined.has(v))
    expect(missing).toEqual([])
  })

  it('defines every status StatTile can be given', () => {
    const tile = readFileSync(resolve(SRC, 'components/StatTile.tsx'), 'utf8')
    const union = tile.match(/status\??:\s*([^\n;]+)/)?.[1] ?? ''
    const statuses = [...union.matchAll(/'([a-z]+)'/g)].map((m) => m[1])
    expect(statuses.length).toBeGreaterThan(0)
    for (const s of statuses) expect(css).toContain(`--status-${s}:`)
  })

  it('defines the custom form classes', () => {
    for (const cls of ['.select', '.input', '.field', '.btn', '.card', '.chip']) {
      expect(css).toContain(cls)
    }
  })

  it('defines every custom text size used', () => {
    const sizes = new Set([...code.matchAll(/\btext-(\dxs)\b/g)].map((m) => m[1]))
    for (const size of sizes) expect(tailwind).toContain(`'${size}'`)
  })
})
