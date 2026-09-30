---
slug: bypoll-context
topic: context
title_en: Why there is a bypoll
title_hi: उपचुनाव की पृष्ठभूमि
sources: HLD 1
last_reviewed: 2026-09-20
in_prompt: true
---

The sitting MLA, Sudivya Kumar "Sonu" (JMM), elected in 2019 and again in 2024,
died on 6 September 2026. The seat is vacant and the Election Commission must
hold a by-election within six months of the vacancy, so roughly by early
March 2027.

Consequences the analysis has to carry:

1. **Sympathy factor.** A bypoll after a sitting member's death usually moves
   votes, but by an amount nobody can measure in advance. It is modelled as a
   scenario input in `analytics/scenario.py`, never as a prediction.
2. **Roll revision.** Confirm whether the bypoll roll is post-Special Intensive
   Revision (SIR). If it is, deletions per booth matter as much as additions and
   should be read as a first-class metric.
3. **Timeline.** Phases 0-2 of the build (data acquisition, core dashboard,
   caste module) should be complete before the ECI announcement.
