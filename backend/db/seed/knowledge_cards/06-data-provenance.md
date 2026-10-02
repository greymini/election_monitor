---
slug: data-provenance
topic: compliance
title_en: Where each number comes from
title_hi: आंकड़ों का स्रोत
sources: HLD 4 | LLD 4
last_reviewed: 2026-10-02
in_prompt: true
---

Citation rules for every answer:

| Figure | Source of truth | How to cite |
|---|---|---|
| Booth-wise votes | Form 20 (CEO Jharkhand); loaded for VS-2024 and VS-2019 | `source_doc` and `source_page` on `result_booth_meta`; file sha256 in `source_doc` |
| Constituency result | Form 20 "Total Votes Polled" row (EVM + postal) | `result_ac_total.source` |
| Candidate's party | `db/seed/form20/candidate_parties.csv`; Form 20 prints names only | say "party not in source" when it is UNK |
| Electors, additions, deletions | Mother roll and supplementary lists | `roll_revision.label` |
| Caste share | Derived estimate | community, `confidence`, `source` |
| Local election winner | SEC Jharkhand | `local_result.source_doc`, plus `tag_source` for the party tag |
| News claim | The article itself | URL and publication date |

Things this system cannot answer, and should say so plainly:

- How any individual voted, or any individual's community.
- Booth results for elections whose Form 20 has not been loaded (only VS-2019
  and VS-2024 are; VS-2005 may not be online at all).
- Postal ballots by booth: Form 20 reports them for the whole constituency
  only, so every booth figure is EVM votes.
- Booth locations, electorates and turnout per booth: no polling-station list
  or roll is loaded yet, so these are "data not available", not zero.
- Panchayat candidates' true party affiliation - those polls are party-less and
  any tag is a manual judgement with a recorded source.
- Anything about a future result. Scenarios are arithmetic on stated
  assumptions, not forecasts.

When the data does not support an answer, say "data not available" and name what
would be needed.
