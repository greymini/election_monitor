# Database schema available to `run_sql`

Read-only. Only the tables listed here are readable; anything else is refused by
the guard. Always add a `LIMIT` (one is imposed at 500 regardless).

## Start here — the wide result view

`mv_result_booth_wide` — one row per election × booth. This answers most result
questions on its own.

| column | meaning |
|---|---|
| `election_id`, `election_label`, `election_type`, `election_year` | `'VS-2024'`, `'LS-2024 (AC seg)'`; type is `VS`/`LS`/`PANCHAYAT`/`WARD` |
| `booth_uid` | stable booth id, e.g. `B0042`. **Join on this, never on ps_number** |
| `area_id`, `block_id` | panchayat or ward, and block |
| `ps_numbers` | the polling-station number(s) that fed this row in that year |
| `electors`, `total_valid`, `votes_counted` | roll size and votes |
| `jmm bjp ajsu jlkm inc rjd jvm others nota` | votes per party |
| `winner_party`, `winner_votes`, `runner_party`, `runner_votes` | |
| `margin_votes`, `margin_pct`, `turnout_pct` | |
| `source_doc`, `source_page` | cite these |

## Geography

- `block (block_id, name_en, name_hi, kind)` — 1 = Giridih Municipal
  Corporation (`ulb`), 2 = Giridih Block, 3 = Pirtand Block (`rural`).
- `area (area_id, block_id, kind, name_en, name_hi, code)` — `kind` is
  `'panchayat'` or `'ward'`.
- `booth (booth_uid, area_id, ps_name_hi, building, village_or_locality,
  current_ps_number, geocode_conf)`.
- `booth_crosswalk (election_id, ps_number, booth_uid, match_method, confidence,
  reviewed)` — how a year's PS number maps to a booth. **`confidence < 0.85`
  means any multi-year comparison for that booth is provisional; say so.**

## Elections

- `election (election_id, type, year, label, is_baseline)` — `is_baseline` is
  true for VS-2024, the reference for swing and scenarios.
- `party (party_id, abbr, name_en, name_hi, alliance_2024, colour)`.
- `candidate (candidate_id, election_id, name_en, party_id, is_winner)`.

## Analysis views

- `mv_booth_party_share (election_id, type, year, booth_uid, party_id, votes,
  share_pct)` — long form, one row per party per booth.
- `mv_swing (election_id, type, year, booth_uid, party_id, share_pct,
  prev_share_pct, swing_pct, swing_votes)` — versus the previous election **of
  the same type**.
- `mv_transfer_ls_vs (year, booth_uid, party_id, party, ls_votes, vs_votes,
  delta_votes, ls_share_pct, vs_share_pct, delta_share_pct)` — the LS↔VS split.
- `mv_floating_vote (year, booth_uid, floating_pct)` — Pedersen index between
  the LS and VS polls of that year. High = genuinely movable vote.
- `mv_volatility (booth_uid, margin_stddev, margin_avg, distinct_winners)`.
- `mv_new_voter_share (booth_uid, area_id, electors_now, additions, deletions,
  net_change, add_18_19, add_female, del_death, del_shifted, new_voter_pct,
  deleted_pct)`.
- `mv_booth_priority (booth_uid, area_id, margin_pct, new_voter_pct,
  margin_stddev, floating_pct, priority_score, priority_quartile)` —
  `priority_score` is 0-100, higher = more worth working.
- `mv_area_rollup (election_id, area_id, area_name_en, area_name_hi, area_kind,
  block_id, booths, electors, votes_counted, jmm … nota, turnout_pct, jmm_pct,
  bjp_pct, jmm_minus_bjp)`.

## Rolls (counts only — no voter records exist)

- `roll_revision (revision_id, revision_date, label, is_post_sir, is_mother)`.
- `roll_snapshot (revision_id, booth_uid, electors, male, female, other,
  age_18_19 … age_60p)`.
- `roll_change (revision_id, booth_uid, additions, deletions, modifications,
  add_18_19, add_female, del_death, del_shifted, del_other)`.

## Demography and community estimates

- `demography (area_id, census_year, population, sc, st, literate,
  main_workers, households)` — Census 2011.
- `community (community_id, name_en, name_hi, category)` — category is
  `GEN`/`OBC`/`SC`/`ST`/`MUSLIM`/`OTHER`.
- `caste_estimate (booth_uid, community_id, est_count, est_pct, confidence,
  source)` — **estimates**. `source` is `surname`, `census`, `survey` or
  `blend`; use `source = 'blend'` unless asked otherwise, and always filter
  `confidence >= 0.4` or report the confidence alongside.

## Local elections

- `local_result (election_id, seat_type, area_id, seat_name, winner, runner_up,
  tagged_party_id, tag_source, tag_confidence, votes, margin)` — panchayat polls
  are party-less, so `tagged_party_id` is a manual judgement. Always mention
  `tag_source` when you use it.

## Worked queries

```sql
-- Twenty booths where BJP led by most in VS-2024
SELECT booth_uid, bjp, jmm, (bjp - jmm) AS lead
FROM mv_result_booth_wide
WHERE election_label = 'VS-2024'
ORDER BY lead DESC LIMIT 20;

-- New voters per panchayat since the baseline roll
SELECT a.name_hi, SUM(n.additions) AS additions,
       ROUND(100.0 * SUM(n.additions) / NULLIF(SUM(n.electors_now), 0), 2) AS pct
FROM mv_new_voter_share n JOIN area a ON a.area_id = n.area_id
WHERE a.kind = 'panchayat'
GROUP BY a.name_hi ORDER BY pct DESC LIMIT 40;

-- Booths that backed AJSU in LS-2024 but JMM in VS-2024
SELECT booth_uid, ls_share_pct, vs_share_pct
FROM mv_transfer_ls_vs
WHERE year = 2024 AND party = 'AJSU' AND delta_share_pct < -15
ORDER BY delta_share_pct LIMIT 50;
```

## Rules for writing SQL here

- Join booths across years on `booth_uid`, never `ps_number`.
- Filter elections by `election_label`, which is unambiguous.
- Use `NULLIF(x, 0)` in every denominator.
- Aggregate before you rank; do not rank inside a filtered subset and then
  re-filter.
- Ask for the columns you need. `SELECT *` on a wide view wastes the row budget.
