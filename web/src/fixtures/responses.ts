/**
 * Fixture responses, shaped exactly like the API's.
 *
 * `lib/api.ts` routes here when `VITE_FIXTURES=1`, so no page contains any
 * fixture-handling code: the pages make the same calls and render the same
 * shapes either way. That is the point - a page reviewed against fixtures is
 * the same page that will run against Postgres, not a mock of it.
 */

import {
  ACS,
  BOOTHS,
  BOOTHS_2019,
  CONFIG,
  FIXTURE_BANNER,
} from './index'

const SOURCE_DOC = 'form20-vs2024-ac32.pdf'

const valid = (b: (typeof BOOTHS)[number]) =>
  b.jmm + b.bjp + b.jlkm + b.others + b.nota

/** Winner and runner-up among real candidates. NOTA never ranks. */
function ranked(b: (typeof BOOTHS)[number]) {
  const contenders: Array<[string, number]> = [
    ['JMM', b.jmm], ['BJP', b.bjp], ['JLKM', b.jlkm], ['OTH', b.others],
  ]
  contenders.sort((x, y) => y[1] - x[1] || x[0].localeCompare(y[0]))
  return { winner: contenders[0], runner: contenders[1] }
}

function boothRow(b: (typeof BOOTHS)[number]) {
  const v = valid(b)
  const { winner, runner } = ranked(b)
  const marginVotes = winner[1] - runner[1]
  const marginPct = Math.round((1000 * marginVotes) / v) / 10
  // Signed by Giridih's contest pair, JMM/BJP. NULL if neither won - which is
  // different from zero, and is why a third-party win greys on the map.
  const signed =
    winner[0] === 'JMM' ? marginPct : winner[0] === 'BJP' ? -marginPct : null

  const prev = BOOTHS_2019[b.booth_uid]
  // Swing is withheld for three separate reasons, each present in the fixtures.
  const swingWithheld =
    !prev ? 'no prior election loaded for this booth'
      : b.lineage_kind ? `booth was ${b.lineage_kind} and the lineage group is not aggregated`
        : (!b.crosswalk_reviewed && (b.crosswalk_confidence ?? 0) < 0.85)
          ? `crosswalk confidence ${b.crosswalk_confidence} is unreviewed and below 0.85`
          : null
  const prevValid = prev ? prev.jmm + prev.bjp + prev.jvm + prev.nota : null
  const jmmSwing =
    swingWithheld || !prev || !prevValid
      ? null
      : Math.round(
        (1000 * b.jmm) / v - (1000 * prev.jmm) / prevValid,
      ) / 10

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
    votes_polled: v + b.rejected,
    rejected: b.rejected,
    nota: b.nota,
    jmm: b.jmm, bjp: b.bjp, jlkm: b.jlkm, others: b.others,
    ajsu: null, inc: null, rjd: null, jvm: null,
    winner_party: winner[0],
    runner_party: runner[0],
    margin_votes: marginVotes,
    margin_pct: marginPct,
    signed_margin_pct: signed,
    // NULL when electors are unknown (B4), with the reason carried alongside.
    turnout_pct: b.electors
      ? Math.round((1000 * (v + b.rejected)) / b.electors) / 10
      : null,
    turnout_null_reason: b.electors
      ? null
      : 'no roll snapshot is linked to this election, so the electorate is unknown',
    jmm_swing_pct: jmmSwing,
    swing_null_reason: swingWithheld,
    new_voter_pct: b.additions && b.electors
      ? Math.round((1000 * b.additions) / b.electors) / 10
      : null,
    new_voter_null_reason: b.additions && b.electors
      ? null
      : 'no roll revision is linked to both ends of the window',
    additions: b.additions,
    floating_pct: b.floating_pct,
    floating_null_reason: b.floating_pct === null
      ? 'only one poll type is loaded for this year, so a Pedersen index is undefined'
      : null,
    margin_stddev: b.margin_stddev,
    volatility_null_reason: b.margin_stddev === null
      ? 'fewer than two assembly years are loaded for this booth'
      : null,
    crosswalk_confidence: b.crosswalk_confidence,
    crosswalk_reviewed: b.crosswalk_reviewed,
    lineage_kind: b.lineage_kind,
    // Provenance: every loaded number names its document and page.
    source_doc: SOURCE_DOC,
    source_page: b.source_page,
  }
}

const ROWS = BOOTHS.map(boothRow)

function acRows(acNumber: number) {
  return acNumber === 32 ? ROWS : []
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
    elections: loaded
      ? [
        {
          label: 'VS-2024', type: 'VS', year: 2024, is_baseline: true,
          booths: rows.length,
          votes: rows.reduce((s, r) => s + r.valid_votes, 0),
          // Published, not summed: one booth has no linked roll, so a booth
          // sum would be short by its electorate and silently wrong.
          electors: 304898,
          total_valid: 207598, nota: 2004,
          winner_party: 'JMM', winner_votes: 94042,
          runner_party: 'BJP', runner_votes: 90204,
          margin_votes: 3838,
          has_results: true,
          source_doc: SOURCE_DOC,
        },
        {
          label: 'VS-2019', type: 'VS', year: 2019, is_baseline: false,
          booths: 7, votes: 168000, electors: 264814,
          total_valid: null, nota: null,
          winner_party: 'JMM', winner_votes: 80871,
          runner_party: 'BJP', runner_votes: 64987,
          margin_votes: 15884, has_results: true,
          source_doc: 'form20-vs2019-ac32.pdf',
        },
        {
          label: 'LS-2024', type: 'LS', year: 2024, is_baseline: false,
          booths: 2, votes: 52000, electors: null,
          total_valid: null, nota: null,
          winner_party: 'AJSU', winner_votes: null,
          runner_party: 'JMM', runner_votes: null,
          margin_votes: null, has_results: true,
          source_doc: 'form20-ls2024-pc11.pdf',
        },
      ]
      : [],
    baseline: loaded
      ? { label: 'VS-2024', jmm: 94042, bjp: 90204, jlkm: 10787, nota: 2004,
        electors: 304898, votes: 207598 }
      : null,
    // The data-health strip. Every dataset reports loaded / partial / missing,
    // so a page can name the command that fills the gap.
    data_health: {
      booths: acNumber === 32 ? 8 : 0,
      booths_geocoded: acNumber === 32 ? 7 : 0,
      ps_list_rows: acNumber === 32 ? 9 : 0,
      elections_with_results: acNumber === 32 ? 3 : 0,
      open_reviews: acNumber === 32 ? 2 : 0,
      weak_crosswalks: acNumber === 32 ? 1 : 0,
      crosswalk_rows: acNumber === 32 ? 9 : 0,
      latest_roll: acNumber === 32 ? '2026-07-01' : null,
      roll_revisions: acNumber === 32 ? 2 : 0,
      caste_rows: acNumber === 32 ? 8 : 0,
      census_rows: 0,
      local_result_rows: acNumber === 32 ? 4 : 0,
      source_docs: acNumber === 32 ? 3 : 0,
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
      matched_pct: 71.1,
      // Booth 6 is deliberately below the 0.4 floor, so the greying can be seen.
      confidence: i === 5 ? 0.31 : Math.round((0.52 + (i % 3) * 0.07) * 100) / 100,
      source: 'blend',
      method_version: 'surname+census v2',
    })),
  )
}

/** Party share at a booth, for the caste scatter's y-axis. */
function partyShare(uid: string, party: 'JMM' | 'BJP' | 'JLKM') {
  const row = ROWS.find((r) => r.booth_uid === uid)
  if (!row) return null
  const votes = party === 'JMM' ? row.jmm : party === 'BJP' ? row.bjp : row.jlkm
  return Math.round((1000 * votes) / row.valid_votes) / 10
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

/** Resolve a path, including the `/acs/{n}/...` scoped ones. */
export function fixtureFor(path: string): unknown | undefined {
  const clean = path.split('?')[0]
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
        blocks: acNumber === 32
          ? [
            { block_id: 3201, name_en: 'Giridih Municipal Corporation', name_hi: 'गिरिडीह नगर निगम', kind: 'ulb' },
            { block_id: 3202, name_en: 'Giridih Block', name_hi: 'गिरिडीह प्रखंड', kind: 'rural' },
            { block_id: 3203, name_en: 'Pirtand Block', name_hi: 'पीरटांड़ प्रखंड', kind: 'rural' },
          ]
          : [],
        areas: acNumber === 32
          ? [...new Set(BOOTHS.map((b) => b.area_en))].map((name, i) => ({
            area_id: i + 1, block_id: 3201 + (i % 3), kind: i < 2 ? 'ward' : 'panchayat',
            name_en: name, name_hi: BOOTHS.find((b) => b.area_en === name)!.area_hi,
            code: null, booths: BOOTHS.filter((b) => b.area_en === name).length,
          }))
          : [],
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
    case rest === '/booths':
      return {
        type: 'FeatureCollection',
        features: rows.map((r) => ({
          type: 'Feature',
          geometry: r.lon !== null && r.lat !== null
            ? { type: 'Point', coordinates: [r.lon, r.lat] }
            : null,
          properties: r,
        })),
        meta: {
          count: rows.length,
          ungeocoded: rows.filter((r) => r.lat === null).length,
          electors_known: rows.filter((r) => r.electors !== null).length,
          metric: 'signed_margin_pct',
          ac_number: acNumber,
          contest: acNumber === 32 ? { party_a: 'JMM', party_b: 'BJP' } : null,
        },
        fixture: FIXTURE_BANNER,
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
          area_id: 1, area_en: row.area_en, area_hi: row.area_hi, area_kind: 'panchayat',
          block_id: 3202, block_en: row.block_en, block_hi: row.block_en,
        },
        results: [
          {
            election_label: 'VS-2024', election_type: 'VS', election_year: 2024,
            electors: row.electors, valid_votes: row.valid_votes,
            votes_polled: row.votes_polled, turnout_pct: row.turnout_pct,
            jmm: row.jmm, bjp: row.bjp, jlkm: row.jlkm, others: row.others,
            nota: row.nota, winner_party: row.winner_party,
            runner_party: row.runner_party, margin_votes: row.margin_votes,
            margin_pct: row.margin_pct, signed_margin_pct: row.signed_margin_pct,
            source_doc: row.source_doc, source_page: row.source_page,
            ps_numbers: row.ps_numbers,
          },
          ...(prev
            ? [{
              election_label: 'VS-2019', election_type: 'VS', election_year: 2019,
              electors: null, valid_votes: prev.jmm + prev.bjp + prev.jvm + prev.nota,
              votes_polled: null, turnout_pct: null,
              jmm: prev.jmm, bjp: prev.bjp, jlkm: null, others: prev.jvm,
              nota: prev.nota, winner_party: prev.jmm > prev.bjp ? 'JMM' : 'BJP',
              runner_party: prev.jmm > prev.bjp ? 'BJP' : 'JMM',
              margin_votes: Math.abs(prev.jmm - prev.bjp),
              margin_pct: null, signed_margin_pct: null,
              source_doc: 'form20-vs2019-ac32.pdf', source_page: 2,
              ps_numbers: row.ps_numbers,
            }]
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
          priority_score: row.margin_stddev === null ? 0.42 : 0.71,
          priority_quartile: 2,
          inputs_used: row.margin_stddev === null
            ? ['closeness', 'new_voter_pct']
            : ['closeness', 'new_voter_pct', 'volatility'],
          weight_used: row.margin_stddev === null ? 0.6 : 0.8,
        },
        crosswalk: [{
          election_label: 'VS-2019', ps_number: Number(row.ps_numbers.split(',')[0]),
          confidence: row.crosswalk_confidence, reviewed: row.crosswalk_reviewed,
          match_method: row.lineage_kind ?? 'fuzzy',
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
              (1000 * rs.reduce((s, r) => s + r.votes_polled, 0))
              / rs.reduce((s, r) => s + (r.electors ?? 0), 0),
            ) / 10
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
            ? Math.round((1000 * (r.additions ?? 0) * 0.18) / r.electors) / 10
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
          priority_score: r.margin_stddev === null ? 0.42 : 0.71,
          priority_quartile: r.margin_stddev === null ? 3 : 1,
          winner_party: r.winner_party, runner_party: r.runner_party,
          inputs_used: r.margin_stddev === null
            ? ['closeness', 'new_voter_pct'] : ['closeness', 'new_voter_pct', 'volatility'],
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
        rows: acNumber !== 32 ? [] : [
          { news_id: 1, published: '2026-09-28', source: 'Prabhat Khabar',
            title: 'पारसनाथ में जलापूर्ति को लेकर प्रदर्शन',
            summary_hi: 'पारसनाथ क्षेत्र के चार गांवों में पेयजल आपूर्ति ठप।',
            summary_en: 'Protest over stalled drinking-water supply in four Parasnath villages.',
            issues: ['water'], parties: [], sentiment: -1, labelled_at: '2026-09-28',
            url: 'https://example.invalid/1', area_names: ['Parasnath'] },
          { news_id: 2, published: '2026-09-26', source: 'Dainik Bhaskar',
            title: 'JLKM की पदयात्रा', summary_hi: 'पचंबा में पदयात्रा।',
            summary_en: 'JLKM padyatra through Pachamba ward.',
            issues: ['candidate/organisation'], parties: ['JLKM'], sentiment: 1,
            labelled_at: '2026-09-26', url: 'https://example.invalid/2',
            area_names: ['Ward 4'] },
          // An unlabelled item, which is what the stream looks like with no
          // Anthropic key: keyword-tagged only, and the UI must say so.
          { news_id: 3, published: '2026-09-29', source: 'Hindustan',
            title: 'गिरिडीह में सड़क निर्माण', summary_hi: null, summary_en: null,
            issues: [], parties: [], sentiment: null, labelled_at: null,
            url: 'https://example.invalid/3', area_names: [] },
        ],
        count: acNumber === 32 ? 3 : 0, fixture: FIXTURE_BANNER,
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
