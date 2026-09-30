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

export const FIXTURE_BANNER =
  'Fixture data. Booth-level figures are synthetic, built to reconcile to the ' +
  'published AC totals so the pages can be reviewed before a Form 20 is loaded. ' +
  'No figure here is evidence of anything.'

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

interface BoothFixture {
  booth_uid: string
  ps_numbers: string
  area_en: string
  area_hi: string
  block_en: string
  building: string
  lat: number | null
  lon: number | null
  electors: number | null
  jmm: number
  bjp: number
  jlkm: number
  others: number
  nota: number
  rejected: number
  source_page: number
  crosswalk_confidence: number | null
  crosswalk_reviewed: boolean
  lineage_kind: string | null
  /** Additions since the 2019 roll, or null where no roll is linked. */
  additions: number | null
  /** Pedersen index, or null where only one poll type is loaded. */
  floating_pct: number | null
  margin_stddev: number | null
}

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
export const BOOTHS: BoothFixture[] = [
  {
    booth_uid: '32-B0001', ps_numbers: '1', area_en: 'Ward 4', area_hi: 'वार्ड 4',
    block_en: 'Giridih Municipal Corporation', building: 'Primary School Pachamba',
    lat: 24.1912, lon: 86.3051, electors: 41200,
    jmm: 12105, bjp: 13980, jlkm: 1420, others: 1380, nota: 268, rejected: 14,
    source_page: 3, crosswalk_confidence: 1.0, crosswalk_reviewed: true,
    lineage_kind: null, additions: 5210, floating_pct: 59.45, margin_stddev: 8.05,
  },
  {
    booth_uid: '32-B0002', ps_numbers: '2', area_en: 'Ward 7', area_hi: 'वार्ड 7',
    block_en: 'Giridih Municipal Corporation', building: 'Middle School Barganda',
    lat: 24.1854, lon: 86.3094, electors: 38900,
    jmm: 11480, bjp: 12760, jlkm: 1310, others: 1290, nota: 251, rejected: 9,
    source_page: 3, crosswalk_confidence: 0.97, crosswalk_reviewed: true,
    lineage_kind: null, additions: 4880, floating_pct: 51.20, margin_stddev: 6.42,
  },
  {
    booth_uid: '32-B0003', ps_numbers: '3', area_en: 'Chatro', area_hi: 'चतरो',
    block_en: 'Giridih Block', building: 'Primary School Chatro',
    // No geocode: the map must report it as unplaced, not omit it.
    lat: null, lon: null, electors: 36400,
    jmm: 12890, bjp: 10240, jlkm: 1080, others: 1420, nota: 243, rejected: 11,
    source_page: 4, crosswalk_confidence: 0.94, crosswalk_reviewed: true,
    lineage_kind: null, additions: 4610, floating_pct: null, margin_stddev: 9.11,
  },
  {
    booth_uid: '32-B0004', ps_numbers: '4', area_en: 'Chatro', area_hi: 'चतरो',
    block_en: 'Giridih Block', building: 'Panchayat Bhawan Chatro',
    lat: 24.2011, lon: 86.2887, electors: 39750,
    jmm: 13240, bjp: 11020, jlkm: 1490, others: 1350, nota: 260, rejected: 7,
    source_page: 4, crosswalk_confidence: 0.91, crosswalk_reviewed: true,
    lineage_kind: null, additions: 5020, floating_pct: null, margin_stddev: 7.80,
  },
  {
    booth_uid: '32-B0005', ps_numbers: '5', area_en: 'Madhuban', area_hi: 'मधुबन',
    block_en: 'Pirtand Block', building: 'Primary School Madhuban',
    lat: 24.0455, lon: 86.1402,
    // No linked roll snapshot: electors, turnout and new-voter share are all
    // NULL. This is the B4 and B1 shape, and the page must say why.
    electors: null,
    jmm: 12610, bjp: 10880, jlkm: 1610, others: 1390, nota: 249, rejected: 12,
    source_page: 5, crosswalk_confidence: 0.96, crosswalk_reviewed: true,
    lineage_kind: null, additions: null, floating_pct: null, margin_stddev: 5.90,
  },
  {
    booth_uid: '32-B0006', ps_numbers: '6', area_en: 'Harladih', area_hi: 'हरलाडीह',
    block_en: 'Pirtand Block', building: 'Middle School Harladih',
    lat: 24.0612, lon: 86.1755, electors: 37100,
    jmm: 11920, bjp: 10410, jlkm: 1450, others: 1330, nota: 241, rejected: 8,
    source_page: 5,
    // 0.71 and unreviewed: the booth appears, its swing is withheld (B2).
    crosswalk_confidence: 0.71, crosswalk_reviewed: false,
    lineage_kind: null, additions: 4490, floating_pct: null, margin_stddev: null,
  },
  {
    booth_uid: '32-B0007', ps_numbers: '7,8', area_en: 'Parasnath', area_hi: 'पारसनाथ',
    block_en: 'Pirtand Block', building: 'Primary School Parasnath',
    lat: 23.9640, lon: 86.1330, electors: 42300,
    jmm: 10990, bjp: 11480, jlkm: 1360, others: 1310, nota: 246, rejected: 10,
    source_page: 6, crosswalk_confidence: 0.88, crosswalk_reviewed: true,
    // A split: the swing is withheld until the lineage group is aggregated.
    lineage_kind: 'split', additions: 5350, floating_pct: null, margin_stddev: null,
  },
  {
    booth_uid: '32-B0008', ps_numbers: '9', area_en: 'Khukhra', area_hi: 'खुखरा',
    block_en: 'Giridih Block', building: 'Anganwadi Khukhra',
    lat: 24.2244, lon: 86.2501, electors: 29248,
    jmm: 8807, bjp: 9434, jlkm: 1067, others: 1091, nota: 246, rejected: 6,
    source_page: 6, crosswalk_confidence: 1.0, crosswalk_reviewed: true,
    // No 2019 comparator at all: swing NULL for a third reason (D2).
    lineage_kind: null, additions: 4374, floating_pct: null, margin_stddev: null,
  },
]

/** 2019, for the booths that have a comparator. Keyed by booth_uid. */
export const BOOTHS_2019: Record<string, { jmm: number; bjp: number; jvm: number; nota: number }> = {
  '32-B0001': { jmm: 10980, bjp: 9920, jvm: 1180, nota: 210 },
  '32-B0002': { jmm: 10240, bjp: 9110, jvm: 1090, nota: 198 },
  '32-B0003': { jmm: 11310, bjp: 7480, jvm: 1240, nota: 205 },
  '32-B0004': { jmm: 11720, bjp: 8010, jvm: 1300, nota: 214 },
  '32-B0005': { jmm: 11040, bjp: 7920, jvm: 1210, nota: 201 },
  '32-B0006': { jmm: 10410, bjp: 7610, jvm: 1150, nota: 193 },
  '32-B0007': { jmm: 9680, bjp: 8240, jvm: 1080, nota: 188 },
}
