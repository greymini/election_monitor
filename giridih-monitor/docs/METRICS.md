# METRICS.md — canonical metric definitions

Every figure this system displays is defined here, once. The table is master
prompt §3.2 verbatim, with the location of each implementation added.

Two implementations exist on purpose and must agree:

- **`analytics/metrics.py`** — Python, used by the loaders, the validator and
  the unit tests. Its docstrings are the specification.
- **`db/migrations/0015_metrics.sql`** — SQL, the same definitions over whole
  tables, which is what the API reads.

`tests/test_metrics.py` checks the Python against hand-computed fixtures.
`tests/e2e/test_metrics_sql.py` runs the same fixtures through the SQL so the
two cannot drift. Where they disagree, the Python docstring is the specification
and the SQL is the bug.

> Every figure here should be checked against the source document before it is
> relied on. The system makes that a one-click check; it does not remove the
> responsibility.

---

## The table

| Metric | Definition | NULL when | Python | SQL |
|---|---|---|---|---|
| `valid_votes` | Σ candidate votes **including NOTA** (= Form 20 "total valid votes") | no result | `metrics.valid_votes` | `mv_booth_totals.valid_votes` |
| `votes_polled` | `valid_votes + rejected` (tendered excluded) | no result | `metrics.votes_polled` | `mv_result_booth_wide.votes_polled` |
| `turnout_pct` | `votes_polled / electors × 100` (electors from the linked roll snapshot; fallback the PS list; **never** Form 20) | electors unknown | `metrics.turnout_pct` | `mv_result_booth_wide.turnout_pct` |
| `share_pct` | `candidate_votes / valid_votes × 100` | no result | `metrics.share_pct` | `mv_booth_party_share.share_pct` |
| `margin_votes` | winner − runner-up, both real candidates (NOTA never ranks) | fewer than 2 candidates | `metrics.margin_votes` | `mv_result_booth_wide.margin_votes` |
| `margin_pct` | `margin_votes / valid_votes × 100` | as above | `metrics.margin_pct` | `mv_result_booth_wide.margin_pct` |
| `signed_margin_pct` | `+margin_pct` if the contest pair's first party wins, `−` if the second, NULL if neither (map ramp) | as above | `metrics.signed_margin_pct` | `mv_result_booth_wide.signed_margin_pct` |
| `swing_pct` (party, booth) | `share_now − share_prev` for the **same election type** and **same booth_uid** via the crosswalk | no prior election, or crosswalk row `reviewed=false AND confidence<0.85`, or booth split/merged without aggregation | `metrics.swing_pct` | `mv_swing.swing_pct` |
| `alliance_swing_pct` | same, summing parties by `party_alliance` **for each event separately** | as above | `metrics.alliance_swing_pct` | `mv_swing` + `party_alliance` |
| `new_voter_pct` | additions in window / electors at window end × 100; window = (linked roll of previous GE, linked roll of target] | either roll missing | `metrics.new_voter_pct` | `mv_new_voter_share.new_voter_pct` |
| `net_roll_change_pct` | (additions − deletions) / electors at window start × 100 | either roll missing | `metrics.net_roll_change_pct` | `mv_new_voter_share.net_roll_change_pct` |
| `transfer_delta` (party) | `share_VS − share_LS`, same year, same booth | either leg missing | `metrics.transfer_delta` | `mv_transfer_ls_vs.delta_share_pct` |
| `floating_pct` | Pedersen index ½ Σ\|Δshare\| across parties, LS vs VS same year | either leg missing | `metrics.floating_pct` | `mv_floating_vote.floating_pct` |
| `volatility` | stdev of `signed_margin_pct` over available VS years | fewer than 2 years | `metrics.volatility` | `mv_volatility.margin_stddev` |
| `priority_score` | weighted **per-AC** percentile ranks: 0.35 closeness (1 − margin percentile), 0.25 `new_voter_pct`, 0.20 `floating_pct`, 0.20 `volatility`; missing inputs dropped and weights renormalised, with `inputs_used` stored | all inputs missing | `metrics.priority_score` | `mv_booth_priority.priority_score` |

The **contest pair** for the signed margin and the scenario is configured per AC
per event in `ac_contest(ac_id, event_id, party_a, party_b)` — Giridih JMM/BJP,
Silli JMM/AJSU, Dumri JLKM/JMM, Kanke INC/BJP. It is not hardcoded anywhere.

---

## The two rules behind every entry

### Absent is NULL, never zero and never a default

This is not a style preference. Each of the following was a number the audited
system displayed that looked like data and was not:

| What was shown | Why | Finding |
|---|---|---|
| A **+38.3 point BJP swing** at every booth in the earliest loaded election | `LAG` COALESCEd to 0, so a party's whole vote share became its "swing" | D2 |
| **`floating_pct = 50.00` at every booth in the constituency** | A Pedersen index over one poll type is 100/2. It appeared on the map, in the transfer view, on the booth card and in the priority score, and because it was uniform `PERCENT_RANK` flattened it so the priority score silently lost its 0.20 floating-vote term | D4 |
| **0 additions everywhere**, while another screen showed the real figures | `mv_new_voter_share`'s baseline CTE was empty because nothing wrote `election_roll_link`. Two contradictory answers for one metric, neither flagged | B1 |
| **`margin_pct = 100.00` for every booth**, winner NULL | Form 20 columns never resolved to a party, so all candidates collapsed into one unattributed bucket | C1, D3 |
| Turnout **NULL everywhere**, while the map still offered it and greyed every booth | `result_booth_meta.electors` had no writer | B4 |

A zero and a NULL mean different things and the distinction is load-bearing:

- `share_prev = None` → the party did not contest, or we do not know. No swing.
- `share_prev = 0.0` → the party contested and polled nothing. A real swing.

### One denominator: valid votes including NOTA

The ECI's published margin percentage divides by total valid votes with NOTA
among them. Giridih 2024: a 3,838-vote margin over 207,598 valid votes is the
published **1.85%**. Excluding NOTA's 2,004 votes gives 1.87%.

The audited views divided by a NOTA-excluding total while displaying a
NOTA-including one in the same row, so the headline margin was wrong in the
second decimal and no row reconciled against itself: `jmm + bjp + … + others +
nota ≠ votes_counted`, off by exactly the NOTA count, in the UI and in the CSV
export (D1).

NOTA now loads as a real candidate row with `party = NOTA`, which is what puts
it in the denominator. It is still not a contestant: it never wins, never places
runner-up, and never takes part in a swing or a transfer.

---

## Things that are easy to get wrong

**Ranking is per candidate, not per party bucket.** Eight independents on 500
votes each are eight candidates, not one 4,000-vote party. The audited view
grouped on `COALESCE(party_id, -1)`, so that bucket competed for winner and
could outrank a real winner on 3,000 votes, producing a NULL winner and a margin
measured against a candidate who does not exist (D3).

**Alliance swing uses each event's own alliance map.** AJSU was outside the NDA
in 2019 and inside it in 2014 and 2024; JVM existed in 2019 and had merged into
BJP by 2024. Using one event's alliances for both attributes a party's votes to
a bloc it was not in.

**Percentiles are computed within one AC.** Kanke has 4,81,815 electors against
Giridih's 3,04,898. A percentile across the pooled set would rank Kanke's booths
high on electorate size alone and call that priority.

**A weak crosswalk does not carry a comparison.** A station matched at 0.70
appears in every view — it has a `booth_crosswalk` row with its real confidence
and `reviewed=false`, which is the fix for B2, where such stations had no row at
all and every view inner-joined that table, so their votes vanished from every
rollup with no error. But its swing is NULL until a human reviews the match.

**A split or merged booth compares on the lineage group.** Half a booth's
electorate against the whole of last time's is not a swing. `booth_lineage`
records the relationship and `weight`; swing is NULL until the group is
aggregated, and the UI flags it.

**Crosswalk coverage is measured against `ps_list_entry`.** Measuring it against
`booth_crosswalk` is circular: the stations that failed to get a row were absent
from both the numerator and the denominator, so the check reported ~100% healthy
while 17% of the constituency had been dropped from every view (C11).

**Area turnout needs every booth's electors.** `mv_area_rollup.turnout_pct` is
NULL unless every booth in the area knows its electorate, because a partial
denominator gives a percentage that is quietly wrong rather than visibly absent.

---

## Checking a figure by hand

Giridih VS-2024, from the published result:

```
JMM   94,042        valid_votes = 94,042 + 90,204 + 10,787 + 2,004 (NOTA) + 10,561 (others)
BJP   90,204                    = 207,598
JLKM  10,787        margin_votes = 94,042 − 90,204 = 3,838
NOTA   2,004        margin_pct   = 100 × 3,838 / 207,598 = 1.849% → 1.85
electors 3,04,898   turnout_pct  = 100 × 207,598 / 304,898 = 68.09%
```

`python -m ingest.validate --ac 32 --strict` recomputes these from the base
tables and asserts they equal both the view values and the published figures. If
it does not exit zero, do not publish the numbers.
