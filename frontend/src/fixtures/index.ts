/**
 * Fixture data, so every page can be reviewed without a real Form 20.
 *
 * Nothing has been loaded into a database yet - there is no Form 20 PDF in
 * `raw/` and no Postgres in the build environment - so the only way to review
 * the dashboard is against fixtures. These are shaped exactly like the API's
 * responses, and are served by `lib/api.ts` when `VITE_FIXTURES=1`, which
 * means the pages themselves contain no fixture-handling code at all: they make
 * the same calls either way.
 *
 * Three rules these fixtures exist to exercise, because they are the ones most
 * easily got wrong and hardest to spot:
 *
 *   1. **Every number carries its provenance.** A result figure names the
 *      source document and page; an estimate names its method and confidence.
 *      A figure with neither is a figure nobody can check.
 *   2. **NULL renders as an em dash with a reason.** Not 0, not blank, not
 *      "N/A". Every audit finding in the D series was a number that looked like
 *      data: a fabricated swing, a uniform 50% floating vote, 0 additions
 *      everywhere. The fixtures deliberately include NULLs of every kind so the
 *      rendering can be reviewed.
 *   3. **AC-awareness.** AC-32 has booth-level data; the other five have
 *      AC-level rows only, or nothing. Switching constituency must change every
 *      page, and the five must read as "not loaded" rather than as empty or
 *      broken.
 *
 * The figures for AC-32 are the published 2024 result (HLD §1.1) decomposed
 * across eight synthetic booths so the AC totals reconcile: the booth sums add
 * to 94,042 / 90,204 / 10,787 / 2,004 and the margin comes out at 1.85%. They
 * are **synthetic booth-level numbers built to match a real AC total**, which is
 * the opposite of real booth data, and every fixture response says so in a
 * `fixture` field that the UI surfaces as a banner.
 */

import {
  AC_TOTALS,
  AC_TOTALS_2019,
  AREAS,
  BOOTH_COUNT,
  GENERATED_BOOTHS,
  GENERATED_BOOTHS_2019,
} from './generated'
import type { GeneratedBooth } from './generated'

export const FIXTURE_BANNER =
  'Fixture data. Votes are the real ECI Form 20 (VS-2024, VS-2019) for all 367 ' +
  'polling stations; each booth\'s electorate, location, ward or panchayat, roll ' +
  'and community figures are synthetic, because no PS list or roll is loaded.'

export interface FixtureSource {
  /** Where a loaded number came from. */
  source_doc?: string | null
  source_page?: number | null
  /** Where a derived number came from instead. */
  method?: string | null
  confidence?: number | null
}

// --------------------------------------------------------------------------
// Constituencies
// --------------------------------------------------------------------------

export const ACS = [
  {
    ac_id: 1, ac_number: 32, name_en: 'Giridih', name_hi: 'गिरिडीह',
    reservation: 'GEN', verified: true, bypoll_due: '2027-03-06',
    vacancy_date: '2026-09-06', districts: 'Giridih', pc_number: 11,
    pc_name_en: 'Giridih', blocks: 3, booths: 8, elections_with_results: 2,
    roll_revisions: 2, election_label: 'VS-2024', winner_party: 'JMM',
    runner_party: 'BJP', margin_votes: 3838, margin_pct: 1.85,
    turnout_pct: 68.09, electors: 304898, valid_votes: 207598,
    jlkm_share_pct: 5.2, new_voter_pct: 13.15, crosswalk_coverage_pct: 100,
    accessible: true, has_booth_data: true,
    notes: 'Sitting MLA died 6 Sep 2026; ECI must poll within six months.',
  },
  {
    ac_id: 2, ac_number: 31, name_en: 'Gandey', name_hi: 'गांडेय',
    reservation: 'GEN', verified: false, bypoll_due: null, vacancy_date: null,
    districts: 'Giridih', pc_number: 4, pc_name_en: 'Kodarma',
    blocks: 2, booths: 0, elections_with_results: 0, roll_revisions: 0,
    // Nothing loaded: every headline figure is null, not zero.
    election_label: null, winner_party: null, runner_party: null,
    margin_votes: null, margin_pct: null, turnout_pct: null, electors: null,
    valid_votes: null, jlkm_share_pct: null, new_voter_pct: null,
    crosswalk_coverage_pct: null, accessible: true, has_booth_data: false,
    notes: 'Spec 1: AC number is from the 2008 delimitation and must be confirmed.',
  },
  {
    ac_id: 3, ac_number: 33, name_en: 'Dumri', name_hi: 'डुमरी',
    reservation: 'GEN', verified: false, bypoll_due: null, vacancy_date: null,
    districts: 'Giridih / Bokaro', pc_number: 11, pc_name_en: 'Giridih',
    blocks: 3, booths: 0, elections_with_results: 0, roll_revisions: 0,
    // AC-level totals only: a margin with no booths behind it.
    election_label: 'VS-2024', winner_party: 'JLKM', runner_party: 'JMM',
    margin_votes: 10945, margin_pct: null, turnout_pct: null, electors: null,
    valid_votes: null, jlkm_share_pct: null, new_voter_pct: null,
    crosswalk_coverage_pct: null, accessible: true, has_booth_data: false,
    notes: 'Spec 1: spans two districts; confirm the block list from the PS list.',
  },
  {
    ac_id: 4, ac_number: 42, name_en: 'Tundi', name_hi: 'टुंडी',
    reservation: 'GEN', verified: false, bypoll_due: null, vacancy_date: null,
    districts: 'Dhanbad', pc_number: 11, pc_name_en: 'Giridih',
    blocks: 3, booths: 0, elections_with_results: 0, roll_revisions: 0,
    election_label: null, winner_party: null, runner_party: null,
    margin_votes: null, margin_pct: null, turnout_pct: null, electors: null,
    valid_votes: null, jlkm_share_pct: null, new_voter_pct: null,
    crosswalk_coverage_pct: null, accessible: true, has_booth_data: false,
    notes: 'Spec 1: confirm the AC number and parent PC from the PS list.',
  },
  {
    ac_id: 5, ac_number: 61, name_en: 'Silli', name_hi: 'सिल्ली',
    reservation: 'GEN', verified: false, bypoll_due: null, vacancy_date: null,
    districts: 'Ranchi', pc_number: 7, pc_name_en: 'Ranchi',
    blocks: 3, booths: 0, elections_with_results: 0, roll_revisions: 0,
    election_label: null, winner_party: null, runner_party: null,
    margin_votes: null, margin_pct: null, turnout_pct: null, electors: null,
    valid_votes: null, jlkm_share_pct: null, new_voter_pct: null,
    crosswalk_coverage_pct: null, accessible: true, has_booth_data: false,
    notes: 'Spec 1: confirm the AC number from the PS list.',
  },
  {
    ac_id: 6, ac_number: 65, name_en: 'Kanke', name_hi: 'कांके',
    reservation: 'SC', verified: false, bypoll_due: null, vacancy_date: null,
    districts: 'Ranchi', pc_number: 7, pc_name_en: 'Ranchi',
    blocks: 3, booths: 0, elections_with_results: 0, roll_revisions: 0,
    election_label: 'VS-2024', winner_party: 'INC', runner_party: 'BJP',
    margin_votes: 968, margin_pct: null, turnout_pct: null, electors: 481815,
    valid_votes: null, jlkm_share_pct: null, new_voter_pct: null,
    crosswalk_coverage_pct: null, accessible: true, has_booth_data: false,
    notes: 'Spec 1: reserved seat; confirm the ward list from the PS list.',
  },
]

export const CONFIG = {
  chat_enabled: false,
  acs: ACS.map((a) => ({
    ac_number: a.ac_number, name_en: a.name_en, name_hi: a.name_hi,
    reservation: a.reservation, verified: a.verified,
  })),
  default_ac: 32,
  version: '0.2.0-fixtures',
  build_time: '2026-09-30T00:00:00Z',
  fixture: FIXTURE_BANNER,
}

// --------------------------------------------------------------------------
// AC-32 booths. Eight of them, summing to the published 2024 AC totals.
// --------------------------------------------------------------------------


/**
 * The eight booths. Deliberately varied so each rendering rule has a case:
 *
 *   - 32-B0003 has no geocode, so the map must count it as unplaced rather
 *     than dropping it silently.
 *   - 32-B0005 has no linked roll snapshot, so electors, turnout and
 *     new-voter share are all NULL - the B4 and B1 shapes.
 *   - 32-B0006 is a 0.71 unreviewed crosswalk, so its swing must be withheld.
 *   - 32-B0007 is a split, so its swing must be withheld for a different reason.
 *   - 32-B0008 has no 2019 comparator at all.
 *   - Only 32-B0001 and 32-B0002 have an LS leg, so floating_pct is NULL for
 *     the other six rather than 50.00.
 */
/**
 * Booths, from the generated fixture.
 *
 * These were eight hand-written booths averaging 38,000 electors each - an
 * entire assembly segment per polling station. Every per-booth figure on every
 * screen was therefore implausible by a factor of forty, and the map's
 * electorate-scaled marker sizes meant nothing at all.
 *
 * Now the 367 real polling stations, with synthetic electorates of 800-1,500 across the real 36 municipal wards and
 * 24 synthetic rural areas, reconciling exactly to the published constituency
 * totals. Generated from `fixtures/giridih.py`, which `tests/metric_cases.py`
 * also imports - so the frontend fixture and the metric tests cannot disagree
 * about the same constituency, which is what finding N8 was.
 */
// Typed as the generated shape, not the old hand-written `BoothFixture`.
// The generated rows carry the derived metrics too - valid_votes,
// margin_pct, turnout_pct and the rest - and casting to the narrower
// interface hid them, so every consumer recomputed what was already there.
export const BOOTHS: GeneratedBooth[] = GENERATED_BOOTHS

export const BOOTHS_2019: Record<string, {
  jmm: number; bjp: number; others: number; nota: number; source_page: number
}> = GENERATED_BOOTHS_2019

/** Areas and blocks, for the /areas endpoint's filter lists. */
export const AREAS_FIXTURE = AREAS

export { AC_TOTALS, AC_TOTALS_2019, BOOTH_COUNT }
export type { GeneratedBooth }

