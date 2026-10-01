/**
 * Constituency results as the API serves them since the real Form 20 load.
 *
 * `/summary` and `/elections/{label}/candidates` describe the declared result:
 * EVM votes from the booth rows plus postal ballots, which Form 20 reports only
 * for the whole constituency. Pages read these types rather than re-deriving
 * totals from the booth table, which leaves postal ballots out.
 */

import { useQuery } from '@tanstack/react-query'

import type { AcState } from './ac'
import { api } from './api'

/** The party code for a candidate the loaded sources give no party for. */
export const UNRECORDED_PARTY = 'UNK'

export interface SourceDoc {
  source_doc: string
  booth_rows: number
  first_page: number | null
  last_page: number | null
  sha256: string | null
  kind: string | null
  storage_key: string | null
  parse_status: string | null
  synthetic: boolean
}

export interface BoothsLed {
  party: string | null
  candidate: string | null
  booths: number
}

export interface PublishedResult {
  winner_candidate: string | null
  winner_party: string | null
  winner_votes: number | null
  runner_candidate: string | null
  runner_party: string | null
  runner_votes: number | null
  margin_votes: number | null
  source: string | null
}

export interface ElectionRow {
  label: string
  type: string
  year: number
  is_baseline: boolean
  booths: number | null
  /** Votes polled: valid (NOTA included) + rejected. */
  votes: number | null
  electors: number | null
  /** 'roll' when every booth has a linked roll; 'published' when the AC
   *  figure from public reporting stands in. */
  electors_source?: 'roll' | 'published' | null
  /** Valid votes, NOTA included (METRICS.md). */
  total_valid: number | null
  nota: number | null
  rejected?: number | null
  evm_votes?: number | null
  postal_votes?: number | null
  votes_polled_published?: number | null
  winner_party: string | null
  winner_candidate?: string | null
  winner_votes: number | null
  winner_evm_votes?: number | null
  runner_party: string | null
  runner_candidate?: string | null
  runner_votes: number | null
  runner_evm_votes?: number | null
  contestants?: number | null
  margin_votes: number | null
  /** From metric_margin_pct on the server. Not derived here: the formula is
   *  defined once, in analytics/metric_sql.py. */
  margin_pct: number | null
  /** From metric_turnout_pct on the server, for the same reason. */
  turnout_pct: number | null
  has_results?: boolean
  source_doc?: string | null
  source_page?: number | null
  sources?: SourceDoc[]
  /** Whether the booth rows came from generated test documents. */
  synthetic?: boolean | null
  booths_led?: BoothsLed[]
  /** The published result, for an election with no Form 20 loaded. */
  published?: PublishedResult | null
}

export interface CandidateResult {
  candidate_id: number
  candidate: string
  candidate_hi: string | null
  party: string | null
  party_name: string | null
  party_name_hi: string | null
  party_recorded: boolean
  is_winner: boolean
  rank: number
  /** Null when no Form 20 is loaded for the election. */
  evm_votes: number | null
  postal_votes: number | null
  votes: number
  share_pct: number | null
  booths_led: number | null
  deposit_forfeited: boolean | null
  behind_winner: number | null
  source: string | null
}

export interface ElectionCandidates {
  election: { label: string; type: string; year: number }
  basis: 'form20' | 'published'
  valid_votes: number
  votes_polled_by_candidates: number
  candidates: CandidateResult[]
  nota: { votes: number; evm_votes: number | null; postal_votes: number | null;
          share_pct: number | null; source: string | null } | null
  sources: SourceDoc[]
  deposit_rule: string
  notes: string[]
}

export function useElectionCandidates(ac: AcState, label: string | null | undefined) {
  return useQuery<ElectionCandidates>({
    queryKey: ['election-candidates', ac.acNumber, label],
    queryFn: () => api.get(ac.path(`/elections/${encodeURIComponent(label as string)}/candidates`)),
    enabled: ac.acNumber !== null && !!label,
  })
}

/** "Sudivya Kumar (JMM)", or the name alone when the party is not recorded. */
export function candidateLabel(name: string | null | undefined,
                               party: string | null | undefined): string {
  if (!name) return party && party !== UNRECORDED_PARTY ? party : '—'
  const code = party?.includes(':') ? party.split(':')[0] : party
  return code && code !== UNRECORDED_PARTY ? `${name} (${code})` : name
}

/** A contestant key ("UNK:Name", "IND:Name") reduced to its party code. */
export function partyCode(contestant: string | null | undefined): string | null {
  if (!contestant) return null
  return contestant.includes(':') ? contestant.split(':')[0] : contestant
}

/**
 * The candidate who led the most polling stations, when that is not the
 * winner: the seat can be won on fewer booths with bigger margins in them
 * plus postal ballots, which is worth saying out loud.
 */
export function boothLeaderUpset(row: ElectionRow): { leader: BoothsLed; winner: BoothsLed | null } | null {
  const led = row.booths_led ?? []
  if (!led.length || !row.winner_candidate) return null
  const leader = [...led].sort((a, b) => b.booths - a.booths)[0]
  if (leader.candidate === row.winner_candidate) return null
  const winner = led.find((r) => r.candidate === row.winner_candidate) ?? null
  return { leader, winner }
}
