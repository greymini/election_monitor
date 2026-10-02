/**
 * Fixture responses, shaped exactly like the API's.
 *
 * `lib/api.ts` routes here when `VITE_FIXTURES=1`, so no page contains any
 * fixture-handling code: the pages make the same calls and render the same
 * shapes either way. That is the point - a page reviewed against fixtures is
 * the same page that will run against Postgres, not a mock of it.
 */

import {
  AC_TOTALS,
  AC_TOTALS_2019,
  ACS,
  AREAS_FIXTURE,
  BOOTHS,
  BOOTHS_2019,
  CONFIG,
  FIXTURE_BANNER,
} from './index'
// Built by scripts/build_boundaries.py: the real AC and block layers plus the
// synthetic AC-32 areas. Typed loosely on purpose - inferring literal types
// for 99 kB of coordinates buys nothing.
import boundaryFile from './boundaries.json'
import { BOOTH_VOTES, CANDIDATES, CANDIDATE_COLUMNS } from './generated'

const SOURCE_DOC = AC_TOTALS.source_doc
const SOURCE_DOC_2019 = AC_TOTALS_2019.source_doc

/**
 * One booth's candidates by name, ranked, from the Form 20 row. The ranking is
 * the views' (votes, then contestant key), so names agree with winner_party.
 */
function boothCandidates(election: string, uid: string) {
  const votes = BOOTH_VOTES[election]?.[uid]
  if (!votes) return []
  const columns = CANDIDATE_COLUMNS[election]
  const nota = votes[votes.length - 1]
  const valid = votes.reduce((a, b) => a + b, 0)
  const key = (c: { candidate: string; party: string }) =>
    c.party === 'IND' || c.party === 'UNK' ? `${c.party}:${c.candidate}` : c.party
  const rows = columns.map((c, i) => ({
    candidate: c.candidate, party: c.party, contestant: key(c), votes: votes[i],
    share_pct: valid ? Math.round((10000 * votes[i]) / valid) / 100 : null,
  }))
  rows.sort((a, b) => b.votes - a.votes || a.contestant.localeCompare(b.contestant))
  return [...rows, {
    candidate: 'NOTA', party: 'NOTA', contestant: 'NOTA', votes: nota,
    share_pct: valid ? Math.round((10000 * nota) / valid) / 100 : null,
  }]
}

function boothNames(election: string, uid: string) {
  const ranked = boothCandidates(election, uid).filter((c) => c.party !== 'NOTA' && c.votes > 0)
  return {
    winner_candidate: ranked[0]?.candidate ?? null,
    runner_candidate: ranked[1]?.candidate ?? null,
    contestants: ranked.length,
  }
}

/** Polling stations led per candidate, as /summary returns them. */
function boothsLed(election: string) {
  const counts = new Map<string, { party: string; candidate: string; booths: number }>()
  for (const uid of Object.keys(BOOTH_VOTES[election] ?? {})) {
    const top = boothCandidates(election, uid).find((c) => c.party !== 'NOTA')
    if (!top) continue
    const entry = counts.get(top.candidate)
      ?? { party: top.contestant, candidate: top.candidate, booths: 0 }
    entry.booths += 1
    counts.set(top.candidate, entry)
  }
  return [...counts.values()].sort((a, b) => b.booths - a.booths)
}

const DEPOSIT_RULE = 'Deposit forfeited: not elected and not more than one sixth of the '
  + 'valid votes polled by all candidates (RP Act 1951, s.158). NOTA votes are excluded '
  + 'from that total.'

/** GET /elections/{label}/candidates, from the generated declared result. */
function electionCandidates(acNumber: number, election: string) {
  const declared = acNumber === 32 ? CANDIDATES[election] : undefined
  if (!declared) return undefined
  const totals = election === 'VS-2024' ? AC_TOTALS : AC_TOTALS_2019
  const byCandidates = declared.reduce((s, c) => s + c.votes, 0)
  const led = new Map(boothsLed(election).map((r) => [r.candidate, r.booths]))
  return {
    election: { label: election, type: 'VS', year: Number(election.slice(3)) },
    basis: 'form20',
    valid_votes: totals.valid_votes,
    votes_polled_by_candidates: byCandidates,
    candidates: declared.map((c, i) => ({
      candidate_id: i + 1, candidate: c.candidate, candidate_hi: null,
      party: c.party, party_name: null, party_name_hi: null,
      party_recorded: c.party !== 'UNK', is_winner: i === 0, rank: i + 1,
      evm_votes: c.evm_votes, postal_votes: c.postal_votes, votes: c.votes,
      share_pct: Math.round((10000 * c.votes) / totals.valid_votes) / 100,
      booths_led: led.get(c.candidate) ?? 0,
      deposit_forfeited: i !== 0 && 6 * c.votes <= byCandidates,
      behind_winner: declared[0].votes - c.votes,
      source: `ECI Form 20 (${totals.source_doc})`,
    })),
    nota: {
      votes: totals.nota, evm_votes: null, postal_votes: null,
      share_pct: Math.round((10000 * totals.nota) / totals.valid_votes) / 100,
      source: `ECI Form 20 (${totals.source_doc})`,
    },
    sources: [{
      source_doc: totals.source_doc, booth_rows: Object.keys(BOOTH_VOTES[election]).length,
      first_page: 1, last_page: totals.pages, sha256: totals.sha256, kind: 'form20',
      storage_key: null, parse_status: 'loaded', synthetic: false,
    }],
    deposit_rule: DEPOSIT_RULE,
    notes: [
      "Votes are the Form 20 'Total Votes Polled' row: EVM votes counted at polling "
        + 'stations plus postal ballots, which Form 20 reports only for the whole '
        + 'constituency. Booths led uses EVM votes.',
    ],
    fixture: FIXTURE_BANNER,
  }
}

/**
 * Every derived figure comes from the generated fixture, not from arithmetic
 * here.
 *
 * This file used to recompute valid votes, the ranking, the margin, the signed
 * margin and the swing for each endpoint. Two consequences, both of which were
 * reported from the screens:
 *
 *   * the map tooltip and the booth card could show different numbers for one
 *     booth, because each derived them separately; and
 *   * the arithmetic was a second implementation of formulas that
 *     `analytics/metrics.py` and `metric_sql.py` already define once, rounding
 *     with `Math.round(x * 1000) / 10` where the product rounds to two places.
 *
 * `fixtures/giridih.py` now computes them with `analytics.metrics` itself, at
 * generation time, and everything below reads the stored field.
 */

/** Why this booth carries no swing, for the card's caveat list. The value is
 *  already NULL in the fixture; this is only the explanation. */
function swingWithheldReason(b: (typeof BOOTHS)[number]): string | null {
  if (b.jmm_swing_pct !== null) return null
  if (!BOOTHS_2019[b.booth_uid]) return 'no prior election loaded for this booth'
  if (b.lineage_kind) {
    return `booth was ${b.lineage_kind} and the lineage group is not aggregated`
  }
  if (!b.crosswalk_reviewed && (b.crosswalk_confidence ?? 0) < 0.85) {
    return `crosswalk confidence ${b.crosswalk_confidence} is unreviewed and below 0.85`
  }
  return 'not comparable'
}

function boothRow(b: (typeof BOOTHS)[number]) {
  const v = b.valid_votes
  const marginPct = b.margin_pct
  const signed = b.signed_margin_pct
  const swingWithheld = swingWithheldReason(b)
  const jmmSwing = b.jmm_swing_pct

  return {
    booth_uid: b.booth_uid,
    ps_numbers: b.ps_numbers,
    area_en: b.area_en,
    area_hi: b.area_hi,
    block_en: b.block_en,
    building: b.building,
    lat: b.lat,
    lon: b.lon,
    electors: b.electors,
    valid_votes: v,
    votes_polled: b.votes_polled,
    rejected: b.rejected,
    tendered: b.tendered,
    nota: b.nota,
    jmm: b.jmm, bjp: b.bjp, jlkm: b.jlkm, others: b.others,
    ...boothNames('VS-2024', b.booth_uid),
    ajsu: null, inc: null, rjd: null, jvm: null,
    winner_party: b.winner_party,
    runner_party: b.runner_party,
    margin_votes: b.margin_votes,
    margin_pct: marginPct,
    signed_margin_pct: signed,
    // NULL when electors are unknown (B4), with the reason carried alongside.
    turnout_pct: b.turnout_pct,
    turnout_null_reason: b.turnout_pct === null
      ? 'no roll snapshot is linked to this election, so the electorate is unknown'
      : null,
    // Same field names as the live API: the swing of the AC's contest party A.
    swing_pct: jmmSwing,
    swing_party: 'JMM',
    swing_null_reason: swingWithheld,
    new_voter_pct: b.new_voter_pct,
    new_voter_null_reason: b.new_voter_pct === null
      ? 'no roll revision is linked to both ends of the window'
      : null,
    additions: b.additions,
    floating_pct: b.floating_pct,
    floating_null_reason: b.floating_pct === null
      ? 'only one poll type is loaded for this year, so a Pedersen index is undefined'
      : null,
    margin_stddev: b.margin_stddev,
    volatility_null_reason: b.margin_stddev === null
      ? 'fewer than two assembly years are loaded for this booth'
      : null,
    // From analytics.metrics.priority_score at generation time. This was
    // absent, so the Overview's priority panel filtered out every booth and
    // rendered an empty list.
    priority_score: b.priority_score,
    priority_inputs_used: b.priority_inputs_used,
    priority_weight_used: b.priority_weight_used,
    crosswalk_confidence: b.crosswalk_confidence,
    crosswalk_reviewed: b.crosswalk_reviewed,
    lineage_kind: b.lineage_kind,
    // Provenance: every loaded number names its document and page.
    source_doc: SOURCE_DOC,
    source_page: b.source_page,
  }
}

/**
 * Block and area identity, derived from the generated area list.
 *
 * Areas used to get `block_id: 3201 + (i % 3)` - round-robin - so Ward 2 was
 * filed under Giridih Block, Ward 3 under Pirtand, and choosing a block in the
 * map's picker listed areas from all three. Booth rows carried no ids at all,
 * so nothing could be filtered by them. Both now come from the area's own
 * `block_en`, the same field the generator placed the booth by.
 */
const BLOCK_KIND: Record<string, string> = { ward: 'ulb', panchayat: 'rural' }

export const FIXTURE_BLOCKS = [...new Set(AREAS_FIXTURE.map((a) => a.block_en))].map(
  (blockEn, i) => {
    const first = AREAS_FIXTURE.find((a) => a.block_en === blockEn)!
    return {
      block_id: 3201 + i,
      name_en: blockEn,
      name_hi: first.block_hi,
      kind: BLOCK_KIND[first.kind] ?? 'rural',
    }
  },
)

const BLOCK_ID = new Map<string, number>(FIXTURE_BLOCKS.map((b) => [b.name_en, b.block_id]))

export const FIXTURE_AREAS = AREAS_FIXTURE.map((a, i) => ({
  area_id: i + 1,
  block_id: BLOCK_ID.get(a.block_en)!,
  kind: a.kind,
  name_en: a.area_en,
  name_hi: a.area_hi,
  code: null,
  booths: BOOTHS.filter((b) => b.area_en === a.area_en).length,
}))

const AREA_ID = new Map<string, number>(FIXTURE_AREAS.map((a) => [a.name_en, a.area_id]))

const ROWS = BOOTHS.map((b) => ({
  ...boothRow(b),
  area_id: AREA_ID.get(b.area_en)!,
  block_id: BLOCK_ID.get(b.block_en)!,
}))

function acRows(acNumber: number) {
  return acNumber === 32 ? ROWS : []
}

interface BoundaryFeature {
  type: 'Feature'
  geometry: { type: string; coordinates: unknown }
  properties: Record<string, unknown> & { layer: string; ac_number: number; name_en?: string }
}

const BOUNDARY = boundaryFile as unknown as {
  features: BoundaryFeature[]
  sources: Record<string, unknown>
  warnings: Array<{ ac_number: number; code: string; message: string }>
  fixture_note: string
}

/** /boundaries, shaped as api/routers/data.py returns it, with the fixture's
 *  block and area ids attached so a selected filter can be highlighted. */
function boundariesFor(acNumber: number) {
  const mine = BOUNDARY.features.filter((f) => f.properties.ac_number === acNumber)
  const withIds = (f: BoundaryFeature) => {
    if (acNumber !== 32) return f
    const name = f.properties.name_en ?? ''
    return {
      ...f,
      properties: {
        ...f.properties,
        block_id: f.properties.layer === 'block' ? BLOCK_ID.get(name) ?? null : BLOCK_ID.get(String(f.properties.block_en)) ?? null,
        ...(f.properties.layer === 'area' ? { area_id: AREA_ID.get(name) ?? null } : {}),
      },
    }
  }
  return {
    ac_number: acNumber,
    ac: mine.find((f) => f.properties.layer === 'ac') ?? null,
    blocks: { type: 'FeatureCollection', features: mine.filter((f) => f.properties.layer === 'block').map(withIds) },
    areas: { type: 'FeatureCollection', features: mine.filter((f) => f.properties.layer === 'area').map(withIds) },
    sources: BOUNDARY.sources,
    warnings: BOUNDARY.warnings.filter((w) => w.ac_number === acNumber),
    fixture: FIXTURE_BANNER,
  }
}

/** Metrics defined against the baseline only, as in mv_booth_priority. */
const BASELINE_ONLY = ['new_voter_pct', 'priority_score', 'floating_pct', 'margin_stddev'] as const

/**
 * A booth's result columns for a non-baseline election, mirroring what
 * /booths now joins from mv_result_booth_wide: margin, signed margin and
 * winner for that election; baseline-only metrics NULL rather than borrowed.
 *
 * VS-2019 is the real 2019 Form 20 row for the same PS number. Turnout is
 * NULL because no 2019 roll snapshot exists in the fixture. LS-2024 has no
 * booth rows in the fixture, so every booth is uncoloured for it.
 */
function resultFor(row: (typeof ROWS)[number], election: string) {
  const blank = Object.fromEntries(BASELINE_ONLY.map((k) => [k, null]))
  const prev = election === 'VS-2019' ? BOOTHS_2019[row.booth_uid] : undefined
  if (!prev) {
    return {
      ...row, ...blank, election_label: election, electors: null,
      margin_pct: null, signed_margin_pct: null, turnout_pct: null,
      winner_party: null, runner_party: null,
    }
  }
  const ranked = boothCandidates(election, row.booth_uid).filter((c) => c.party !== 'NOTA')
  const valid = prev.jmm + prev.bjp + prev.others + prev.nota
  const [winner, runner] = ranked
  const margin = valid > 0 ? Math.round((10000 * (winner.votes - runner.votes)) / valid) / 100 : null
  // Signed by the contest pair, JMM positive: any other winner is neither arm.
  const signed = margin === null ? null
    : winner.party === 'JMM' ? margin : winner.party === 'BJP' ? -margin : null
  return {
    ...row, ...blank, election_label: election, electors: null,
    valid_votes: valid, votes_polled: valid, jmm: prev.jmm, bjp: prev.bjp, jlkm: 0,
    others: prev.others, nota: prev.nota, rejected: 0, tendered: 0,
    margin_votes: winner.votes - runner.votes,
    margin_pct: margin, signed_margin_pct: signed, turnout_pct: null,
    winner_party: winner.contestant, runner_party: runner.contestant,
    ...boothNames(election, row.booth_uid),
    source_doc: SOURCE_DOC_2019, source_page: prev.source_page,
  }
}

function electionRow(label: string, year: number, baseline: boolean,
                     t: typeof AC_TOTALS | typeof AC_TOTALS_2019, booths: number) {
  const declared = CANDIDATES[label]
  return {
    label, type: 'VS', year, is_baseline: baseline, booths,
    votes: t.votes_polled, electors: t.electors, electors_source: t.electors_source,
    total_valid: t.valid_votes, nota: t.nota, rejected: t.rejected,
    evm_votes: t.evm_votes, postal_votes: t.postal_votes, votes_polled_published: null,
    winner_party: declared[0].party, winner_candidate: declared[0].candidate,
    winner_votes: declared[0].votes, winner_evm_votes: declared[0].evm_votes,
    runner_party: declared[1].party, runner_candidate: declared[1].candidate,
    runner_votes: declared[1].votes, runner_evm_votes: declared[1].evm_votes,
    contestants: t.contestants, margin_votes: t.margin_votes, margin_pct: t.margin_pct,
    turnout_pct: t.turnout_pct, has_results: true,
    source_doc: t.source_doc, source_page: null,
    sources: [{
      source_doc: t.source_doc, booth_rows: booths, first_page: 1, last_page: t.pages,
      sha256: t.sha256, kind: 'form20', storage_key: null, parse_status: 'loaded',
      synthetic: false,
    }],
    synthetic: false,
    booths_led: boothsLed(label),
    published: null,
  }
}

function summaryFor(acNumber: number) {
  const ac = ACS.find((a) => a.ac_number === acNumber)!
  const rows = acRows(acNumber)
  const loaded = rows.length > 0

  return {
    constituency: {
      ac_number: ac.ac_number,
      code: `AC-${ac.ac_number}`,
      name_en: ac.name_en,
      name_hi: ac.name_hi,
      reservation: ac.reservation,
      verified: ac.verified,
    },
    bypoll: {
      vacancy_date: ac.vacancy_date,
      deadline: ac.bypoll_due,
      days_to_deadline: ac.bypoll_due ? 157 : null,
      note: ac.bypoll_due
        ? 'The ECI must hold the poll within six months of the vacancy.'
        : 'No by-election is pending in this constituency.',
    },
    // The declared results, from the generated Form 20 totals (EVM + postal).
    // VS-2014 has no Form 20 loaded and carries its published result.
    elections: loaded
      ? [
        electionRow('VS-2024', 2024, true, AC_TOTALS, rows.length),
        electionRow('VS-2019', 2019, false, AC_TOTALS_2019, Object.keys(BOOTHS_2019).length),
        {
          label: 'LS-2024', type: 'LS', year: 2024, is_baseline: false, booths: 0,
          votes: null, electors: null, total_valid: null, nota: null,
          winner_party: null, winner_votes: null, runner_party: null, runner_votes: null,
          margin_votes: null, margin_pct: null, turnout_pct: null, has_results: false,
          source_doc: null, sources: [], booths_led: [], synthetic: null, published: null,
        },
        {
          label: 'VS-2014', type: 'VS', year: 2014, is_baseline: false, booths: 0,
          votes: null, electors: null, total_valid: null, nota: null,
          winner_party: null, winner_votes: null, runner_party: null, runner_votes: null,
          margin_votes: null, margin_pct: null, turnout_pct: null, has_results: false,
          source_doc: null, sources: [], booths_led: [], synthetic: null,
          published: {
            winner_candidate: 'Nirbhay Kumar Shahabadi', winner_party: 'BJP',
            winner_votes: 57450, runner_candidate: 'Sudivya Kumar', runner_party: 'JMM',
            runner_votes: 47517, margin_votes: 9933,
            source: 'HLD 1.1 (secondary - re-verify vs Form 20)',
          },
        },
      ]
      : [],
    synthetic: false,
    baseline: loaded
      ? { label: 'VS-2024', jmm: AC_TOTALS.jmm, bjp: AC_TOTALS.bjp, jlkm: AC_TOTALS.jlkm,
        nota: AC_TOTALS.nota, electors: AC_TOTALS.electors, votes: AC_TOTALS.votes_polled }
      : null,
    // The data-health strip. Every dataset reports loaded / partial / missing,
    // so a page can name the command that fills the gap.
    // Counted from `rows`, the same 367-booth source every other figure on
    // every page comes from.
    //
    // These were literals - `booths: 8`, `booths_geocoded: 7`,
    // `ps_list_rows: 9`, `caste_rows: 8` - written when the fixture had eight
    // booths. Replacing the fixture left them behind, so the health cards went
    // on reporting the old constituency while the tables beside them reported
    // the new one, and no amount of restarting a dev server changes a literal.
    // `test_overview_counts.py` now asserts each of these equals the count in
    // the source.
    data_health: {
      booths: rows.length,
      booths_geocoded: rows.filter((r) => r.lat !== null).length,
      // One PS row per booth: the fixture has no re-numbering, so the PS list
      // and the booth list are the same length by construction.
      ps_list_rows: rows.length,
      elections_with_results: acNumber === 32 ? 2 : 0,
      open_reviews: rows.filter((r) => !r.crosswalk_reviewed).length,
      weak_crosswalks: rows.filter(
        (r) => !r.crosswalk_reviewed && (r.crosswalk_confidence ?? 0) < 0.85,
      ).length,
      crosswalk_rows: rows.length,
      latest_roll: acNumber === 32 ? '2026-07-01' : null,
      roll_revisions: acNumber === 32 ? 2 : 0,
      // One community estimate per booth in the fixture.
      caste_rows: rows.length,
      census_rows: 0,
      local_result_rows: acNumber === 32 ? 4 : 0,
      source_docs: acNumber === 32 ? 3 : 0,
      form20_real_docs: acNumber === 32 ? 2 : 0,
      form20_booth_rows: acNumber === 32
        ? rows.length + Object.keys(BOOTHS_2019).length : 0,
    },
    scope: { block_id: null, sees_caste: true },
    fixture: FIXTURE_BANNER,
  }
}

const COMMUNITIES = [
  { en: 'Kurmi (Mahato)', hi: 'कुर्मी (महतो)', category: 'OBC', share: 22.4 },
  { en: 'Muslim', hi: 'मुस्लिम', category: 'MUSLIM', share: 14.1 },
  { en: 'Yadav', hi: 'यादव', category: 'OBC', share: 9.8 },
  { en: 'Santhal', hi: 'संताल', category: 'ST', share: 8.6 },
  { en: 'Baniya', hi: 'बनिया', category: 'GEN', share: 7.2 },
  { en: 'Brahmin', hi: 'ब्राह्मण', category: 'GEN', share: 5.1 },
  { en: 'Turi', hi: 'तुरी', category: 'SC', share: 3.9 },
  // The explicit residual (C14). Not a community: a statement about how much of
  // the electorate the surname dictionary could not place.
  { en: 'UNMATCHED', hi: 'अवर्गीकृत', category: 'OTHER', share: 28.9 },
]

function casteRows(acNumber: number) {
  if (acNumber !== 32) return []
  return BOOTHS.flatMap((b, i) =>
    COMMUNITIES.map((c) => ({
      booth_uid: b.booth_uid,
      area_en: b.area_en,
      area_hi: b.area_hi,
      community_en: c.en,
      community_hi: c.hi,
      category: c.category,
      // Varied per booth so the scatter has spread.
      est_pct: Math.round((c.share + ((i % 4) - 1.5) * 2.1) * 10) / 10,
      est_count: b.electors
        ? Math.round((b.electors * (c.share + ((i % 4) - 1.5) * 2.1)) / 100)
        : null,
      // Booth 6 is deliberately below the 0.4 floor, so the greying can be seen.
      confidence: i === 5 ? 0.31 : Math.round((0.52 + (i % 3) * 0.07) * 100) / 100,
      source: 'blend',
    })),
  )
}

/** Party share at a booth, for the caste scatter's y-axis. */
function partyShare(uid: string, party: 'JMM' | 'BJP' | 'JLKM') {
  const row = ROWS.find((r) => r.booth_uid === uid)
  if (!row) return null
  const votes = party === 'JMM' ? row.jmm : party === 'BJP' ? row.bjp : row.jlkm
  // Two decimal places, matching metrics.share_pct. This rounded to one,
  // so a fixture share differed from the same figure computed anywhere else
  // in the system by up to 0.05 points.
  return Math.round((10000 * votes) / row.valid_votes) / 100
}

export const FIXTURES: Record<string, unknown> = {
  '/config': CONFIG,
  '/acs': {
    acs: ACS, count: ACS.length,
    unverified: ACS.filter((a) => !a.verified).map((a) => a.ac_number),
    note: 'Constituencies marked unverified are seeded from secondary sources.',
    fixture: FIXTURE_BANNER,
  },
  '/compare': {
    rows: ACS.filter((a) => a.election_label).map((a) => ({
      ac_id: a.ac_id, ac_number: a.ac_number, name_en: a.name_en,
      name_hi: a.name_hi, verified: a.verified,
      election_label: a.election_label, election_type: 'VS', year: 2024,
      is_baseline: true, winner_party: a.winner_party,
      runner_party: a.runner_party, margin_votes: a.margin_votes,
      margin_pct: a.margin_pct, signed_margin_pct: a.margin_pct,
      turnout_pct: a.turnout_pct, electors: a.electors,
      valid_votes: a.valid_votes, jlkm_share_pct: a.jlkm_share_pct,
      new_voter_pct: a.new_voter_pct,
      crosswalk_coverage_pct: a.crosswalk_coverage_pct, booths: a.booths,
    })),
    count: 3,
    note: 'Assembly elections only. An empty cell is absence, not zero.',
    fixture: FIXTURE_BANNER,
  },
  '/auth/me': {
    user_id: 1, name: 'Fixture Analyst', role: 'admin', block_id: null,
    sees_caste: true, daily_token_budget: 150000,
  },
}

/**
 * Resolve a request: method, path (with its query string) and body.
 *
 * GET honours the query parameters the pages send - map block/area filters,
 * the caste confidence floor, news search and issue, the results election -
 * where it used to ignore everything after the `?` and answer every variant
 * with the same rows (so a filter looked broken, and VS-2019 showed 2024's
 * figures under a 2019 heading). POST answers the three writes the UI makes,
 * which used to fall through to "No fixture".
 */
/** Mirrors api/routers/news.py ISSUES. */
const NEWS_ISSUES = [
  'water', 'roads', 'electricity', 'health', 'education', 'employment/migration',
  'mining/coal', 'Parasnath/Marang Buru', 'law-and-order', 'welfare-schemes',
  'corruption', 'electoral-roll/SIR', 'candidate/organisation', 'alliance', 'other',
]

export function fixtureFor(path: string, method = 'GET', body?: unknown): unknown | undefined {
  const [clean, search = ''] = path.split('?')
  const query = new URLSearchParams(search)
  if (method.toUpperCase() !== 'GET') return postFixture(clean, body)
  const answer = getFixture(clean, query)
  return answer === undefined ? undefined : applyQuery(clean, query, answer)
}

/** The GET answer for a path. /booths applies its own filters (from rahul-working:
 *  real block and area ids, other elections via resultFor); the rest are
 *  filtered afterwards by applyQuery. */
function getFixture(clean: string, params: URLSearchParams): unknown | undefined {
  if (clean in FIXTURES) return FIXTURES[clean]

  const scoped = clean.match(/^\/acs\/(\d+)(\/.*)?$/)
  if (!scoped) return undefined
  const acNumber = Number(scoped[1])
  const rest = scoped[2] ?? ''
  const rows = acRows(acNumber)

  switch (true) {
    case rest === '' :
      return ACS.find((a) => a.ac_number === acNumber)
    case rest === '/summary':
      return summaryFor(acNumber)
    case rest === '/areas': {
      return {
        ac_number: acNumber,
        blocks: acNumber === 32 ? FIXTURE_BLOCKS : [],
        areas: acNumber === 32 ? FIXTURE_AREAS : [],
        elections: acNumber === 32
          ? [
            { election_id: 1, label: 'VS-2024', type: 'VS', year: 2024, is_baseline: true, has_results: true },
            { election_id: 2, label: 'VS-2019', type: 'VS', year: 2019, is_baseline: false, has_results: true },
            { election_id: 3, label: 'LS-2024', type: 'LS', year: 2024, is_baseline: false, has_results: true },
            { election_id: 4, label: 'VS-2014', type: 'VS', year: 2014, is_baseline: false, has_results: false },
          ]
          : [{ election_id: 9, label: 'VS-2024', type: 'VS', year: 2024, is_baseline: true, has_results: false }],
        parties: [
          { party_id: 1, abbr: 'JMM', name_en: 'Jharkhand Mukti Morcha', name_hi: 'झारखंड मुक्ति मोर्चा', colour: '#1a6b39' },
          { party_id: 2, abbr: 'BJP', name_en: 'Bharatiya Janata Party', name_hi: 'भारतीय जनता पार्टी', colour: '#ff8c42' },
          { party_id: 4, abbr: 'JLKM', name_en: 'Jharkhand Loktantrik Krantikari Morcha', name_hi: 'झारखंड लोकतांत्रिक क्रांतिकारी मोर्चा', colour: '#6d3fc4' },
        ],
        contest: acNumber === 32
          ? { party_a: 'JMM', party_b: 'BJP', source: 'HLD 1.1' }
          : null,
        fixture: FIXTURE_BANNER,
      }
    }
    case rest === '/boundaries':
      return boundariesFor(acNumber)
    case rest === '/booths': {
      // The same filters, in the same way, as api/routers/data.py.
      const blockId = Number(params.get('block_id')) || null
      const areaId = Number(params.get('area_id')) || null
      const metric = params.get('metric') ?? 'margin_pct'
      const election = params.get('election_label') ?? 'VS-2024'
      const scoped = rows
        .filter((r) => blockId === null || r.block_id === blockId)
        .filter((r) => areaId === null || r.area_id === areaId)
        .map((r) => (election === 'VS-2024' ? r : resultFor(r, election)))
      return {
        type: 'FeatureCollection',
        features: scoped.map((r) => ({
          type: 'Feature',
          geometry: r.lon !== null && r.lat !== null
            ? { type: 'Point', coordinates: [r.lon, r.lat] }
            : null,
          properties: r,
        })),
        meta: {
          count: scoped.length,
          ungeocoded: scoped.filter((r) => r.lat === null).length,
          electors_known: scoped.filter((r) => r.electors !== null).length,
          metric,
          election_label: election,
          ac_number: acNumber,
          contest: acNumber === 32 ? { party_a: 'JMM', party_b: 'BJP' } : null,
        },
        fixture: FIXTURE_BANNER,
      }
    }
    case rest.startsWith('/booths/') && rest.endsWith('/card'): {
      const uid = rest.slice('/booths/'.length, -'/card'.length)
      const row = rows.find((r) => r.booth_uid === decodeURIComponent(uid))
      if (!row) return undefined
      const prev = BOOTHS_2019[row.booth_uid]
      return {
        booth: {
          booth_uid: row.booth_uid, ps_name_hi: row.building, building: row.building,
          village_or_locality: row.area_en, current_ps_number: Number(row.ps_numbers.split(',')[0]),
          geocode_conf: row.lat === null ? null : 0.82,
          area_id: row.area_id, area_en: row.area_en, area_hi: row.area_hi,
          area_kind: FIXTURE_AREAS[row.area_id - 1].kind,
          block_id: row.block_id, block_en: row.block_en,
          block_hi: FIXTURE_BLOCKS.find((b) => b.block_id === row.block_id)!.name_hi,
        },
        results: [
          {
            election_label: 'VS-2024', election_type: 'VS', election_year: 2024,
            electors: row.electors, valid_votes: row.valid_votes,
            votes_polled: row.votes_polled, turnout_pct: row.turnout_pct,
            jmm: row.jmm, bjp: row.bjp, jlkm: row.jlkm, others: row.others,
            nota: row.nota, winner_party: row.winner_party,
            winner_candidate: row.winner_candidate, runner_party: row.runner_party,
            contestants: row.contestants, rejected: row.rejected, tendered: row.tendered,
            margin_votes: row.margin_votes,
            margin_pct: row.margin_pct, signed_margin_pct: row.signed_margin_pct,
            source_doc: row.source_doc, source_page: row.source_page,
            ps_numbers: row.ps_numbers,
            candidates: boothCandidates('VS-2024', row.booth_uid),
          },
          ...(prev
            ? [(() => {
              const r = resultFor(row, 'VS-2019')
              return {
                election_label: 'VS-2019', election_type: 'VS', election_year: 2019,
                electors: null, valid_votes: r.valid_votes, votes_polled: r.votes_polled,
                turnout_pct: null, jmm: r.jmm, bjp: r.bjp, jlkm: null, others: r.others,
                nota: r.nota, winner_party: r.winner_party,
                winner_candidate: r.winner_candidate, runner_party: r.runner_party,
                contestants: r.contestants, rejected: 0, tendered: 0,
                margin_votes: r.margin_votes, margin_pct: r.margin_pct,
                signed_margin_pct: r.signed_margin_pct,
                source_doc: SOURCE_DOC_2019, source_page: prev.source_page,
                ps_numbers: row.ps_numbers,
                candidates: boothCandidates('VS-2019', row.booth_uid),
              }
            })()]
            : []),
        ],
        roll: row.electors
          ? [{ revision: '2026-07', revision_date: '2026-07-01', electors: row.electors,
            male: Math.round(row.electors * 0.517), female: Math.round(row.electors * 0.482),
            other: Math.round(row.electors * 0.001), source_doc: 'roll-2026-07-ac32.pdf',
            source_page: 1 }]
          : [],
        new_voters: {
          additions: row.additions, new_voter_pct: row.new_voter_pct,
          null_reason: row.new_voter_null_reason,
        },
        priority: {
          // Was `row.margin_stddev === null ? 0.42 : 0.71` - two made-up
          // numbers switched on whether volatility happened to be missing, so
          // every booth in the constituency showed one of two scores and the
          // "which inputs contributed" line was decorative.
          priority_score: row.priority_score,
          priority_quartile: 2,
          inputs_used: row.priority_inputs_used,
          weight_used: row.priority_weight_used,
        },
        crosswalk: [{
          election_label: 'VS-2019', ps_number: Number(row.ps_numbers.split(',')[0]),
          // The loader's 2019 link: same PS number, 0.95, unreviewed.
          confidence: 0.95, reviewed: false, match_method: 'exact',
        }],
        caste_estimate: casteRows(acNumber)
          .filter((c) => c.booth_uid === row.booth_uid)
          .map((c) => ({ name_en: c.community_en, name_hi: c.community_hi,
            est_pct: c.est_pct, confidence: c.confidence, source: c.source })),
        caste_note:
          'Estimated at booth level from surname inference blended with Census 2011. '
          + 'No individual voter is tagged with a community. UNMATCHED is the share the '
          + 'surname dictionary could not place, not a community.',
        caveats: [
          row.turnout_null_reason,
          row.swing_null_reason,
          row.floating_null_reason,
        ].filter(Boolean) as string[],
        fixture: FIXTURE_BANNER,
      }
    }
    case rest.startsWith('/elections/') && rest.endsWith('/candidates'):
      return electionCandidates(acNumber, decodeURIComponent(rest.split('/')[2]))
    case rest.startsWith('/results/') && rest.endsWith('/booths'):
      return {
        election_label: decodeURIComponent(rest.split('/')[2]),
        ac_number: acNumber, rows, count: rows.length, fixture: FIXTURE_BANNER,
      }
    case rest.startsWith('/results/') && rest.endsWith('/areas'): {
      const byArea = new Map<string, typeof rows>()
      for (const r of rows) byArea.set(r.area_en, [...(byArea.get(r.area_en) ?? []), r])
      return {
        election_label: decodeURIComponent(rest.split('/')[2]),
        ac_number: acNumber,
        rows: [...byArea.entries()].map(([area, rs], i) => ({
          area_id: i + 1, area_name_en: area, area_name_hi: rs[0].area_hi,
          area_kind: 'panchayat', block_id: 3202, block_name_en: rs[0].block_en,
          booths: rs.length,
          electors: rs.every((r) => r.electors !== null)
            ? rs.reduce((s, r) => s + (r.electors ?? 0), 0) : null,
          valid_votes: rs.reduce((s, r) => s + r.valid_votes, 0),
          votes_polled: rs.reduce((s, r) => s + r.votes_polled, 0),
          jmm: rs.reduce((s, r) => s + r.jmm, 0),
          bjp: rs.reduce((s, r) => s + r.bjp, 0),
          jlkm: rs.reduce((s, r) => s + r.jlkm, 0),
          nota: rs.reduce((s, r) => s + r.nota, 0),
          // NULL unless every booth in the area knows its electorate.
          turnout_pct: rs.every((r) => r.electors !== null)
            ? Math.round(
              (10000 * rs.reduce((s, r) => s + r.votes_polled, 0))
              / rs.reduce((s, r) => s + (r.electors ?? 0), 0),
            ) / 100
            : null,
          booths_with_electors: rs.filter((r) => r.electors !== null).length,
        })),
        count: byArea.size, fixture: FIXTURE_BANNER,
      }
    }
    case rest === '/rolls/revisions':
      return {
        rows: acNumber === 32
          ? [
            { revision_id: 2, label: '2026-07', revision_date: '2026-07-01', is_post_sir: true, is_mother: true },
            { revision_id: 1, label: '2024-10', revision_date: '2024-10-01', is_post_sir: false, is_mother: true },
          ]
          : [],
      }
    case rest === '/rolls/changes':
      return {
        ac_number: acNumber,
        rows: rows.filter((r) => r.additions !== null).map((r) => ({
          booth_uid: r.booth_uid, revision: '2026-07', revision_date: '2026-07-01',
          is_post_sir: true, area_en: r.area_en, area_hi: r.area_hi, block_id: 3202,
          additions: r.additions, deletions: Math.round((r.additions ?? 0) * 0.18),
          modifications: 41, add_18_19: Math.round((r.additions ?? 0) * 0.22),
          add_female: Math.round((r.additions ?? 0) * 0.49),
          del_death: 120, del_shifted: 260, del_other: 40,
          electors: r.electors,
          additions_pct: r.new_voter_pct,
          deletions_pct: r.electors
            ? Math.round((10000 * (r.additions ?? 0) * 0.18) / r.electors) / 100
            : null,
          source_doc: 'roll-2026-07-ac32.pdf', source_page: 1,
        })),
        count: rows.filter((r) => r.additions !== null).length,
        fixture: FIXTURE_BANNER,
      }
    case rest === '/caste':
      return {
        rows: casteRows(acNumber), count: casteRows(acNumber).length, min_conf: 0.4,
        disclaimer:
          'Estimates at booth level only, from surname inference blended with Census 2011 '
          + 'proportions. No individual voter is tagged with a community. Anything below '
          + '0.4 confidence is too weak to act on. Associations with vote share are '
          + 'ecological correlations, not statements about how any community voted.',
        fixture: FIXTURE_BANNER,
      }
    case rest === '/caste/correlation':
      return {
        rows: acNumber !== 32 ? [] : BOOTHS.map((b) => {
          const c = casteRows(32).find(
            (x) => x.booth_uid === b.booth_uid && x.community_en === 'Kurmi (Mahato)',
          )!
          return {
            booth_uid: b.booth_uid, area_en: b.area_en,
            community_pct: c.est_pct, confidence: c.confidence,
            jlkm_share_pct: partyShare(b.booth_uid, 'JLKM'),
            jmm_share_pct: partyShare(b.booth_uid, 'JMM'),
            bjp_share_pct: partyShare(b.booth_uid, 'BJP'),
            electors: b.electors,
          }
        }),
        community: 'Kurmi (Mahato)',
        party: 'JLKM',
        caveat:
          'This is an ecological correlation between two booth-level aggregates. It cannot '
          + 'show how any community voted; a relationship here is equally consistent with '
          + 'the opposite behaviour at individual level (Simpson\'s paradox). The community '
          + 'share is itself an estimate with the confidence shown.',
        fixture: FIXTURE_BANNER,
      }
    case rest === '/candidates':
      return {
        rows: acNumber !== 32 ? [] : [
          {
            candidate_id: 1, name_en: 'Sudivya Kumar', name_hi: 'सुदिव्य कुमार',
            party: 'JMM', election_label: 'VS-2024', votes: 94042, share_pct: 45.3,
            is_winner: true, incumbent: true, contests_prior: 2, wins_prior: 2,
            prev_party: null, turncoat: false, deposit_forfeited: false,
            age: 48, education: 'Post Graduate', profession: 'Social work',
            assets_declared: 41200000, liabilities: 3100000,
            criminal_cases: 1, criminal_serious: 0,
            source: 'myneta-unverified', affidavit_url: null,
          },
          {
            candidate_id: 2, name_en: 'Nirbhay Kumar Shahabadi',
            name_hi: 'निर्भय कुमार शाहाबादी', party: 'BJP',
            election_label: 'VS-2024', votes: 90204, share_pct: 43.45,
            is_winner: false, incumbent: false, contests_prior: 3, wins_prior: 2,
            prev_party: 'JVM', turncoat: true, deposit_forfeited: false,
            age: 57, education: 'Graduate', profession: 'Business',
            assets_declared: 78400000, liabilities: 9200000,
            criminal_cases: 0, criminal_serious: 0,
            source: 'myneta-unverified', affidavit_url: null,
          },
          {
            candidate_id: 3, name_en: 'Navin Anand', name_hi: 'नवीन आनंद',
            party: 'JLKM', election_label: 'VS-2024', votes: 10787, share_pct: 5.2,
            is_winner: false, incumbent: false, contests_prior: 0, wins_prior: 0,
            prev_party: null, turncoat: false, deposit_forfeited: true,
            age: 34, education: null, profession: null,
            assets_declared: null, liabilities: null,
            criminal_cases: null, criminal_serious: null,
            source: 'myneta-unverified', affidavit_url: null,
          },
        ],
        note:
          'Candidate profiles are transcribed from affidavit and MyNeta data and are '
          + 'seeded unverified. Assets and cases are as declared by the candidate.',
        fixture: FIXTURE_BANNER,
      }
    case rest === '/local-politics':
      return {
        office_holders: acNumber !== 32 ? [] : [
          { id: 1, office: 'mukhiya', name: 'Renu Devi', area_en: 'Chatro',
            tagged_party: 'JMM', tag_source: 'block in-charge, Sep 2026',
            term_start: '2022-05-01', term_end: null },
          { id: 2, office: 'mukhiya', name: 'Bhola Mahato', area_en: 'Madhuban',
            tagged_party: 'JLKM', tag_source: 'block in-charge, Sep 2026',
            term_start: '2022-05-01', term_end: null },
          { id: 3, office: 'ward', name: 'Imran Ansari', area_en: 'Ward 7',
            tagged_party: null, tag_source: null,
            term_start: '2018-06-01', term_end: null },
        ],
        events: acNumber !== 32 ? [] : [
          { id: 1, occurred_on: '2026-09-18', kind: 'rally', area_en: 'Ward 4',
            title: 'JLKM padyatra through Pachamba',
            effect_party: 'JLKM', effect_sign: 1, source: 'Prabhat Khabar' },
          { id: 2, occurred_on: '2026-09-12', kind: 'scheme_launch', area_en: 'Chatro',
            title: 'Tap-water connections commissioned',
            effect_party: null, effect_sign: null, source: 'DEO press note' },
        ],
        organisations: acNumber !== 32 ? [] : [
          { id: 1, name: 'Kurmi Vikas Manch', kind: 'caste sabha',
            community: 'Kurmi (Mahato)', alignment_party: null },
        ],
        note:
          'Panchayat polls are contested without party symbols, so any party here is a '
          + 'manual tag. tag_source records who assigned it and when. An untagged holder '
          + 'is untagged, not independent.',
        fixture: FIXTURE_BANNER,
      }
    case rest === '/priority':
      return {
        ac_number: acNumber,
        rows: rows.map((r) => ({
          booth_uid: r.booth_uid, area_en: r.area_en, area_hi: r.area_hi,
          block_id: 3202, building: r.building, margin_pct: r.margin_pct,
          margin_votes: r.margin_votes, electors: r.electors,
          turnout_pct: r.turnout_pct, new_voter_pct: r.new_voter_pct,
          additions: r.additions, margin_stddev: r.margin_stddev,
          floating_pct: r.floating_pct,
          priority_score: r.priority_score,
          priority_quartile: r.priority_score === null
            ? null
            : Math.min(4, Math.floor((1 - r.priority_score) * 4) + 1),
          winner_party: r.winner_party, runner_party: r.runner_party,
          inputs_used: r.priority_inputs_used,
        })),
        count: rows.length,
        formula:
          '0.35 x tight margin + 0.25 x new-voter share + 0.20 x volatility + 0.20 x '
          + 'floating vote, each percentile-ranked within this AC. Missing inputs are '
          + 'dropped and the remaining weights renormalised; inputs_used records which '
          + 'contributed.',
        fixture: FIXTURE_BANNER,
      }
    case rest === '/transfer':
      return {
        year: 2024, ac_number: acNumber,
        rows: acNumber !== 32 ? [] : ['JMM', 'BJP', 'AJSU', 'JLKM'].flatMap((party) =>
          rows.slice(0, 2).map((r) => ({
            booth_uid: r.booth_uid, area_en: r.area_en, area_hi: r.area_hi,
            block_id: 3202, party,
            ls_votes: party === 'AJSU' ? 9800 : party === 'JLKM' ? 7400 : 5200,
            vs_votes: party === 'AJSU' ? 0 : party === 'JLKM' ? r.jlkm : r.jmm,
            delta_votes: null,
            ls_share_pct: party === 'AJSU' ? 35.7 : party === 'JLKM' ? 27.5 : 29.3,
            vs_share_pct: party === 'AJSU' ? 0 : party === 'JLKM' ? 5.2 : 45.3,
            delta_share_pct: party === 'AJSU' ? -35.7 : party === 'JLKM' ? -22.3 : 16.0,
            floating_pct: r.floating_pct,
          })),
        ),
        count: acNumber === 32 ? 8 : 0,
        note:
          'Lok Sabha figures are the AC segment of its parliamentary seat, not the whole '
          + 'PC. floating_pct is the Pedersen index between the two polls and is NULL, '
          + 'not 50%, where only one of them is loaded.',
        fixture: FIXTURE_BANNER,
      }
    case rest === '/knowledge-cards':
      return {
        cards: [
          { slug: 'bypoll-context', topic: 'context', is_general: false,
            title_en: 'Why there is a by-election', title_hi: 'उपचुनाव की पृष्ठभूमि',
            body_en: 'The sitting MLA, elected in 2019 and 2024, died on 6 September 2026. '
              + 'The ECI must poll within six months. A sympathy effect after a sitting '
              + 'member dies is real but cannot be measured in advance; it is a scenario '
              + 'input here, never a prediction.',
            body_hi: 'निवर्तमान विधायक, जो 2019 और 2024 में निर्वाचित हुए, का 6 सितंबर 2026 को निधन हो गया।',
            sources: ['HLD 1'], last_reviewed: '2026-09-23', in_prompt: true },
          { slug: 'caste-guardrails', topic: 'method', is_general: true,
            title_en: 'How to read a community estimate', title_hi: 'समुदाय अनुमान कैसे पढ़ें',
            body_en: 'Electoral rolls do not record caste. Every community figure here is '
              + 'inferred from surnames blended with Census 2011 and carries a confidence '
              + 'score. UNMATCHED is the share the dictionary could not place - it is not a '
              + 'community and must not be redistributed.',
            body_hi: 'मतदाता सूची में जाति दर्ज नहीं होती।',
            sources: ['HLD 5', 'METRICS.md'], last_reviewed: '2026-09-23', in_prompt: true },
        ],
        note: 'Curated from public secondary sources. Check every figure against the source.',
        fixture: FIXTURE_BANNER,
      }
    case rest === '/news':
      return {
        // Fixture items are illustrative headlines, not crawled news.
        rows: acNumber !== 32 ? [] : [
          { news_id: 1, published: '2026-09-28', source: 'Prabhat Khabar',
            title: 'पारसनाथ में जलापूर्ति को लेकर प्रदर्शन',
            summary_hi: 'पारसनाथ क्षेत्र के चार गांवों में पेयजल आपूर्ति ठप।',
            summary_en: 'Protest over stalled drinking-water supply in four Parasnath villages.',
            issues: ['water', 'Parasnath/Marang Buru'], parties: [], persons: [], sentiment: -1,
            labelled_at: '2026-09-28', label_method: 'llm', scope: 'ac', relevance: 0.4,
            url: 'https://example.invalid/1', area_names: ['Parasnath'] },
          { news_id: 2, published: '2026-09-26', source: 'Dainik Bhaskar',
            title: 'गिरिडीह उपचुनाव: JLKM की पदयात्रा, झामुमो ने बैठक की',
            summary_hi: null, summary_en: null,
            issues: ['candidate/organisation'], parties: ['JLKM', 'JMM'], persons: [],
            sentiment: null, labelled_at: '2026-09-26', label_method: 'rules', scope: 'ac',
            relevance: 0.8, url: 'https://example.invalid/2', area_names: [] },
          { news_id: 3, published: '2026-09-29', source: 'Hindustan',
            title: 'गिरिडीह में सड़क निर्माण', summary_hi: null, summary_en: null,
            issues: ['roads'], parties: [], persons: [], sentiment: null,
            labelled_at: '2026-09-29', label_method: 'rules', scope: 'ac', relevance: 0.25,
            url: 'https://example.invalid/3', area_names: [] },
          // Jharkhand-wide: names no constituency, shown only with scope=state.
          { news_id: 4, published: '2026-09-27', source: 'Jagran',
            title: 'एसआईआर के विरोध में रांची में भाजपा और कांग्रेस आमने-सामने',
            summary_hi: null, summary_en: null,
            issues: ['electoral-roll/SIR'], parties: ['BJP', 'INC'], persons: [], sentiment: null,
            labelled_at: '2026-09-27', label_method: 'rules', scope: 'state', relevance: 0.6,
            url: 'https://example.invalid/4', area_names: [] },
        ],
        issues: NEWS_ISSUES, fixture: FIXTURE_BANNER,
      }
    case rest === '/news/summary':
      return {
        since: '2026-09-02', days: 30, scope: 'ac',
        totals: acNumber !== 32
          ? { items: 0, political: 0, with_party: 0, rules_labelled: 0, llm_labelled: 0,
              with_tone: 0, unlabelled: 0 }
          : { items: 3, political: 1, with_party: 1, rules_labelled: 2, llm_labelled: 1,
              with_tone: 1, unlabelled: 0 },
        last_crawled: '2026-09-30T08:00:00+05:30',
        parties: acNumber !== 32 ? [] : [
          { party: 'JLKM', items: 1, share_pct: 50 }, { party: 'JMM', items: 1, share_pct: 50 },
        ],
        party_weeks: acNumber !== 32 ? [] : [
          { week: '2026-09-21', party: 'JLKM', items: 1 }, { week: '2026-09-21', party: 'JMM', items: 1 },
        ],
        issues: acNumber !== 32 ? [] : [
          { issue: 'candidate/organisation', items: 1, examples: [
            { news_id: 2, title: 'गिरिडीह उपचुनाव: JLKM की पदयात्रा, झामुमो ने बैठक की',
              url: 'https://example.invalid/2', source: 'Dainik Bhaskar', published: '2026-09-26' }] },
          { issue: 'water', items: 1, examples: [
            { news_id: 1, title: 'पारसनाथ में जलापूर्ति को लेकर प्रदर्शन',
              url: 'https://example.invalid/1', source: 'Prabhat Khabar', published: '2026-09-28' }] },
        ],
        other_items: 0,
        places: acNumber !== 32 ? [] : [
          { place: 'Giridih', name_en: 'Giridih', name_hi: 'गिरिडीह', kind: 'constituency', items: 2 },
          { place: 'Parasnath', name_en: 'Parasnath', name_hi: 'पारसनाथ', kind: 'landmark', items: 1 },
        ],
        top: acNumber !== 32 ? [] : [
          { news_id: 2, published: '2026-09-26', source: 'Dainik Bhaskar',
            title: 'गिरिडीह उपचुनाव: JLKM की पदयात्रा, झामुमो ने बैठक की',
            summary_hi: null, summary_en: null, issues: ['candidate/organisation'],
            parties: ['JLKM', 'JMM'], persons: [], sentiment: null, label_method: 'rules',
            scope: 'ac', relevance: 0.8, url: 'https://example.invalid/2' },
        ],
        fixture: FIXTURE_BANNER,
      }
    case rest === '/news/issues':
      return {
        since: '2026-08-31',
        by_issue: acNumber !== 32 ? [] : [
          { issue: 'water', items: 11, avg_sentiment: -0.8 },
          { issue: 'roads', items: 7, avg_sentiment: -0.4 },
          { issue: 'employment/migration', items: 5, avg_sentiment: -0.6 },
          { issue: 'candidate/organisation', items: 4, avg_sentiment: 0.2 },
        ],
        by_week: [], fixture: FIXTURE_BANNER,
      }
    case rest === '/local-results':
      return { rows: [], count: 0, note: 'No SEC results loaded.', fixture: FIXTURE_BANNER }
    case rest === '/admin/review-queue':
      return {
        rows: acNumber !== 32 ? [] : [
          { id: 1, kind: 'crosswalk', ref: 'VS-2019#PS6', status: 'open',
            note: 'PS 6 -> 32-B0006 at 0.710, between 0.65 and 0.85. The row is loaded '
              + 'with this confidence and reviewed=false, so the booth appears everywhere '
              + 'but its swing is withheld until you confirm or correct it.',
            created_at: '2026-09-29T10:12:00Z', payload: {} },
          { id: 2, kind: 'form20_row', ref: 'form20-vs2024-ac32.pdf#p7', status: 'open',
            note: 'Unparseable numeric cell in the JLKM column; row not loaded.',
            created_at: '2026-09-29T10:12:00Z', payload: {} },
        ],
        count: acNumber === 32 ? 2 : 0,
        open_by_kind: acNumber === 32
          ? [{ kind: 'crosswalk', open: 1 }, { kind: 'form20_row', open: 1 }] : [],
        fixture: FIXTURE_BANNER,
      }
    case rest === '/admin/jobs':
      return { recent: [], last_per_job: [], fixture: FIXTURE_BANNER }
    case rest === '/admin/usage':
      return {
        month_to_date: { cost_usd: 0, input_tokens: 0, output_tokens: 0 },
        cache_hit_rate: null, by_day: [], by_user_today: [], fixture: FIXTURE_BANNER,
      }
    case rest === '/admin/sources':
      return {
        rows: acNumber !== 32 ? [] : [
          { doc_id: 1, kind: 'form20', filename: SOURCE_DOC, sha256: '3f9a2c1e88b4',
            pages: 12, ocr_pages: 0, parse_status: 'loaded',
            fetched_at: '2026-09-20T06:00:00Z', parsed_at: '2026-09-20T06:40:00Z',
            status_changed_at: '2026-09-20T06:41:00Z', status_changed_by: 'ingest.parse_form20',
            storage_backend: 's3', storage_key: 'form20/2024-VS/32/' + SOURCE_DOC },
          { doc_id: 2, kind: 'roll_mother', filename: 'roll-2026-07-ac32.pdf',
            sha256: 'aa11bb22cc33', pages: 840, ocr_pages: 11, parse_status: 'loaded',
            fetched_at: '2026-09-21T06:00:00Z', parsed_at: '2026-09-21T08:10:00Z',
            status_changed_at: '2026-09-21T08:11:00Z', status_changed_by: 'ingest.parse_roll',
            storage_backend: 'local', storage_key: 'roll_mother/32/roll-2026-07-ac32.pdf' },
        ],
        fixture: FIXTURE_BANNER,
      }
    default:
      return undefined
  }
}


// ---------------------------------------------------------------------------
// Query parameters and writes
// ---------------------------------------------------------------------------

type Obj = Record<string, unknown>

function applyQuery(clean: string, query: URLSearchParams, answer: unknown): unknown {
  const rest = clean.replace(/^\/acs\/\d+/, '')
  const data = answer as Obj
  // Rows carry their real block_id and area_id (fixtures/giridih.py).
  const inScope = (row: Obj) => {
    const block = query.get('block_id')
    const area = query.get('area_id')
    if (block && row.block_id !== Number(block)) return false
    if (area && row.area_id !== Number(area)) return false
    return true
  }

  if (rest.startsWith('/results/') && rest.endsWith('/booths')) {
    const label = decodeURIComponent(rest.split('/')[2])
    const rows = label === 'VS-2024'
      ? (data.rows as Obj[]).filter(inScope)
      : label === 'VS-2019'
        ? (data.rows as Obj[]).filter(inScope)
          .map((r) => resultFor(r as (typeof ROWS)[number], 'VS-2019'))
        : []
    return { ...data, rows, count: rows.length }
  }
  if (rest === '/caste') {
    const floor = Number(query.get('min_conf') ?? 0)
    const rows = (data.rows as Obj[]).filter((r) => (Number(r.confidence) || 0) >= floor)
    return { ...data, rows, count: rows.length, min_conf: floor }
  }
  if (rest === '/news') {
    const q = (query.get('q') ?? '').toLowerCase()
    const issue = query.get('issue')
    const party = query.get('party')
    const scope = query.get('scope') ?? 'ac'
    const rows = (data.rows as Obj[]).filter((r) =>
      (scope === 'state' || r.scope !== 'state')
      && (!q || String(r.title).toLowerCase().includes(q))
      && (!issue || ((r.issues as string[] | null) ?? []).includes(issue))
      && (!party || ((r.parties as string[] | null) ?? []).includes(party)))
    if (query.get('sort') === 'relevance') {
      rows.sort((a, b) => Number(b.relevance ?? 0) - Number(a.relevance ?? 0))
    }
    return { ...data, rows, count: rows.length, scope }
  }
  if (rest === '/local-results') {
    const seat = query.get('seat_type')
    const rows = ((data.rows as Obj[]) ?? []).filter((r) => !seat || r.seat_type === seat)
    return { ...data, rows }
  }
  if (rest === '/rolls/changes') {
    const revision = query.get('revision_label')
    const rows = (data.rows as Obj[]).filter((r) => !revision || r.revision === revision)
    return { ...data, rows, count: rows.length }
  }
  return answer
}

function postFixture(clean: string, body: unknown): unknown | undefined {
  const scoped = clean.match(/^\/acs\/(\d+)(\/.*)$/)
  if (!scoped) return undefined
  const acNumber = Number(scoped[1])
  const rest = scoped[2]
  if (/^\/admin\/review-queue\/\d+$/.test(rest)) return { updated: 1 }
  if (rest === '/ground-reports') return { saved: true }
  if (rest === '/scenario') return scenarioFixture(acNumber, (body ?? {}) as Obj)
  return undefined
}

/** Arithmetic on the fixture booths with the same inputs the real engine takes,
 *  deterministic and without noise, so the Scenario page can be reviewed. */
function scenarioFixture(acNumber: number, body: Obj) {
  const rows = acNumber === 32 ? ROWS : []
  const sum = (k: 'jmm' | 'bjp' | 'jlkm' | 'others' | 'nota') =>
    rows.reduce((s, r) => s + (r[k] ?? 0), 0)
  const turnout = Number(body.turnout_multiplier ?? 1)
  const swing = Number(body.sympathy_swing ?? 0)
  const jlkmToBjp = body.jlkm_to_bjp == null ? null : Number(body.jlkm_to_bjp)
  let jmm = sum('jmm')
  let bjp = sum('bjp')
  let jlkm = sum('jlkm')
  if (jlkmToBjp !== null) {
    bjp += jlkm * jlkmToBjp
    jmm += jlkm * (1 - jlkmToBjp)
    jlkm = 0
  }
  const moved = swing > 0 ? bjp * swing : jmm * Math.abs(swing)
  if (swing > 0) { bjp -= moved; jmm += moved } else { jmm -= moved; bjp += moved }
  const votes = { JMM: Math.round(jmm * turnout), BJP: Math.round(bjp * turnout),
    JLKM: Math.round(jlkm * turnout), OTH: Math.round(sum('others') * turnout),
    NOTA: Math.round(sum('nota') * turnout) }
  const point = votes.JMM - votes.BJP
  const band = Math.round(Math.abs(point) * 0.1) + 200
  return {
    booths: rows.length, votes,
    margin: { point, p10: point - band, p50: point, p90: point + band },
    winner: point >= 0 ? 'JMM' : 'BJP', runner_up: point >= 0 ? 'BJP' : 'JMM',
    contest: ['JMM', 'BJP'], contest_margin: point,
    total_votes: Object.values(votes).reduce((a, b) => a + b, 0),
    high_variance_booths: [], draws: Number(body.draws ?? 500),
    assumptions: body, band_label: 'fixture: fixed band, no noise',
    baseline_source: 'VS-2024', ac_verified: acNumber === 32,
    disclaimer: 'Fixture arithmetic for review only - not the real engine and not a forecast.',
    fixture: FIXTURE_BANNER,
  }
}
