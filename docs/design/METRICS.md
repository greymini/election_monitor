# METRICS.md — canonical metric definitions

Every figure this system displays is defined here, once. The table is master
prompt §3.2 verbatim, with the location of each implementation added.

Two implementations exist on purpose, because neither can do the other's job —
the parsers must compute before a transaction commits, and the views must
aggregate over whole tables:

- **`analytics/metrics.py`** — Python, used by the loaders, the validator and
  the unit tests. Its docstrings are the specification.
- **`db/migrations/0015_metrics.sql`** — SQL, the same definitions over whole
  tables, which is what the API reads.

**The formula text, however, exists once.** `analytics/metric_sql.py` declares
one canonical SQL expression per metric and generates a `CREATE FUNCTION` for
each; that block sits inside `0015_metrics.sql` between two markers, and every
view calls the functions instead of restating the arithmetic. Change a formula
and the migration is wrong until you run:

```
python -m analytics.metric_sql
```

`tests/test_metric_parity.py` regenerates the block and fails if the checked-in
migration has drifted, so "implemented exactly once" is enforced rather than
merely intended. It also scans the views for the arithmetic that used to be
inlined — the percentage quotients, the four priority weights, the 0.85
crosswalk threshold, `stddev_samp`, the Pedersen halving — and fails if any of
them reappears outside a function.

This is not tidiness. When this exercise started, the margin quotient
`100.0 * (winner − runner) / NULLIF(valid_votes, 0)` was written out **seven
times** across two views: once for `margin_pct`, twice more inside the signed
variant's `CASE` arms, and the same four again at AC grain. That is how D1
survived review. Correcting the denominator in one arm and not the others is a
one-character omission, and no test tells it apart from correctness.

Where they disagree, the Python docstring is the specification and the SQL is
the bug.

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

Each row's SQL column is produced by the generated function of the same name:
`mv_result_booth_wide.margin_pct` is `metric_margin_pct(...)`, and so on. Two
functions in that set are not metrics in the table but rules extracted from it,
because more than one metric needs them and a second copy is a second place for
them to drift:

| Function | Rule it owns |
|---|---|
| `metric_comparison_allowed` | Whether a booth may be compared against its own past at all: the crosswalk must be reviewed or ≥0.85, and the booth must not have split or merged without its lineage group being aggregated. Shared by `swing_pct`, `swing_votes` and the vanished-party rows. |
| `metric_priority_weight` | How much of the priority weight was available, reported beside the score so that a score renormalised over part of the weight is identifiable rather than looking like the full four-factor ranking. |

---

## How parity is actually checked

Three values must agree for every metric, and the third is what makes this a
correctness check rather than a diff:

```
hand-computed expectation  ==  analytics/metrics.py  ==  metric_*() in SQL
```

The cases live in `tests/metric_cases.py`, worked out on paper from the
definitions above. Two implementations agreeing only proves they are consistent;
the failure mode this project keeps meeting is both of them being wrong in the
same plausible way — a denominator that excludes NOTA, a `COALESCE` to zero
where the answer is unknown.

- `tests/test_metric_parity.py` — the Python side against the expectations, plus
  the generated-block and no-inlined-formula checks. **No database**, so it runs
  in the default suite.
- `tests/e2e/test_metric_parity_sql.py` — the same cases through PostgreSQL,
  asserting all three agree; then a row-by-row recomputation of
  `mv_result_booth_wide` in Python, because a view can call the right function
  with the wrong arguments and every function-level test still passes.

Four of the audit's wrong answers are asserted *as wrong* there, so the tests
degrade loudly rather than silently: the 1.87% D1 denominator, the 50.00%
one-poll Pedersen index, the zero margin at an uncontested booth, and the
ramp-midpoint zero for a third-party win. If a refactor ever makes the correct
and incorrect forms agree, those tests fail on the grounds that they no longer
distinguish anything.

### Two asymmetries the parity work found, since closed

**An absent crosswalk means "cannot compare" on both sides now (N1).** In SQL a
NULL confidence comes from a `LEFT JOIN` that found no `booth_crosswalk` row, so
the booth cannot be shown to be the station it was and no comparison is carried.
Python used to read the same absence as *nothing to gate on*, because `link`
defaulted to `None` and `None` meant anchor — so the two sides gave opposite
answers for the input most likely to arise, and a caller who merely forgot the
argument got swings between booths that had never been matched to each other.
That is the shape of D2.

`link` is now **required and positional** on `comparison_allowed`, `swing_pct`
and `alliance_swing_pct`; omitting it raises `TypeError`. A booth that genuinely
is the station it was passes `metrics.ANCHOR`, which asserts what the old default
assumed — out loud, at the call site, where a reviewer can see it. Refusing on
`None` only helps if the caller is made to supply something, so the required
argument is the half that does the work.

**`mv_ac_summary` ranks candidates now, not party totals (N2).** D3 was fixed at
booth grain — `mv_result_booth_candidate` ranks per candidate, so eight
independents on 500 votes each no longer sum into one 4,000-vote pseudo-party
that outranks a real winner on 3,000. The AC-grain summary was left ranking the
old way over `party_totals`, where every independent shares a NULL `party_id`
and collapses into a single row, so the constituency headline could name a
winner no booth had elected.

It now ranks over a `candidate_totals` CTE on the same grain and the same
`contestant` key as the booth view, and reports `winner_candidate` and
`runner_candidate` beside the party. `party_totals` remains, because a
party-level share is a real aggregate — it was only the *ranking* that must not
bucket. `contestants` is counted over candidates too, so eleven candidates are
eleven contestants and three of them being independents does not make them one.

Two tests hold it: a structural one in the default suite asserting the ranking
reads `candidate_totals`, and the proof in the e2e suite, which sums
`mv_result_booth_candidate` across every booth and asserts the AC headline names
that candidate. Summing the booth table is what the AC winner *means*, so it is
the reference rather than a second opinion.

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
among them. Giridih 2024 (Form 20): a 3,838-vote margin over 2,07,682 valid votes
is the published **1.85%**. Excluding NOTA's 2,004 votes gives 1.87%.

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

### Form 20 column resolution (§3.1)

A header column resolves to a candidate only if **all three** conditions hold:

| Condition | Value | Why |
|---|---|---|
| Whole-name score | ≥ **0.88** | Higher than the crosswalk's 0.85: a mismatched candidate attributes votes to the wrong person, and the AC-total check cannot catch two candidates being swapped |
| Margin over second-best | ≥ **0.05** | Guards against *ambiguity*. Picking the higher of two near-equal scores is a coin flip dressed up as a decision, and two candidates with similar names is exactly when a wrong attribution is least visible. Waived when there is only one candidate |
| First name-part score | ≥ **0.88** | Guards against a *spurious near-match*, which the first two do not catch |

That third condition is not in the original specification and was added because
the first two demonstrably do not achieve what they were for:

```
'Sudhir Kumat'  against the seeded  'Sudivya Kumar'
    whole-string        0.8833    >= 0.88   threshold rule passes
    margin over second  0.3368    >= 0.05   margin rule passes
    first name-part     0.8444    <  0.88   rejected here
```

The whole-string score is carried by `kumat`/`kumar` at 0.92, which drags
`sudhir`/`sudivy` over the line. The floor is on the **first** part, not every
part, because middle parts are what a Form 20 abbreviates: `Nirbhay Kr
Shahabadi` scores 0.5667 on `kr`/`kumar` and must still resolve. Measured across
every header spelling in the fixtures, the first part separates the legitimate
variants (all 1.0) from the spurious match (0.8444) with no exceptions.

**A printed party outranks an inferred name.** If the header carries a party in
brackets, it is resolved through `party_alias` *first* and the name is then used
only to disambiguate within that party's candidates. A party printed in the
header is a statement by the returning officer; a name match is our inference.
Two consequences: a header whose spelling we do not recognise is still rescued
when its party stood exactly one candidate, and a name cannot pull a column onto
a candidate from a different party than the one printed.

Names are compared after `comparable()`, which canonicalises both
transliterations - Devanagari transliteration reinstates inherent vowels, so
`नवीन आनंद` becomes `naveena aananda` against the ECI's `navin anand` and scores
0.854 raw, below the threshold. Canonicalising both sides is the fix; weakening
the threshold would let genuinely different names through.

---

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

Giridih VS-2024, from the Form 20 "Total Votes Polled" row (EVM + postal;
`db/seed/form20/giridih_vs2024_form20.xlsx`):

```
JMM   94,042        valid_votes  = 94,042 + 90,204 + 10,787 + 10,645 (11 others) + 2,004 (NOTA)
BJP   90,204                     = 2,07,682      (EVM 2,05,777 + postal 1,905)
JLKM  10,787        margin_votes = 94,042 − 90,204 = 3,838
NOTA   2,004        margin_pct   = 100 × 3,838 / 2,07,682 = 1.848% → 1.85
rejected 139        votes_polled = 2,07,682 + 139 = 2,07,821
electors 3,04,898   turnout_pct  = 100 × 2,07,821 / 3,04,898 = 68.16%   (electors: published)
```

Booth rows are EVM votes only: Form 20 reports postal ballots for the whole
constituency, so a sum over booths is 2,05,777, and `mv_ac_summary` adds the
postal rows from `result_ac_total` to reach the declared result.

`python -m ingest.validate --ac 32 --strict` recomputes these from the base
tables and asserts they equal both the view values and the published figures. If
it does not exit zero, do not publish the numbers.
