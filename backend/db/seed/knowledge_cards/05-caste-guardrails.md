---
slug: caste-guardrails
topic: compliance
title_en: What the caste module may and may not say
title_hi: जाति अनुमान की सीमाएँ
sources: HLD 5 | LLD 5 | LLD 12
last_reviewed: 2026-09-20
in_prompt: true
---

Electoral rolls do not record caste. Every caste figure in this system is
**derived** and must be presented as an estimate with its confidence band.

Rules that are not negotiable:

- Estimates exist at **booth level and above only**. No individual voter is
  tagged with a community anywhere in the database, logs or model prompts.
- `parse_roll.py` counts surnames into per-booth aggregates and discards the
  names; a unit test asserts no name or EPIC string is persisted.
- Three inputs feed the blend: surname inference against `surname_dict`, Census
  2011 SC/ST village proportions, and a booth in-charge survey where one exists.
  A survey overrides inference. `caste_estimate.source` records which was used.
- Anything below confidence 0.4 renders greyed and labelled
  "अनुमान अपर्याप्त" (estimate insufficient).
- The chatbot refuses individual-voter lookups and individual caste attribution
  outright, and caveats every aggregate it reports.

Communities of relevance in Giridih, to be confirmed by the ground team:
Kurmi/Mahato, Yadav, Baniya (Barnwal/Sahu), upper castes (Brahmin, Rajput,
Bhumihar), Muslims (large urban presence), SC (Turi, Dusadh, Rajwar, Bhuiyan)
and ST (Santhal, significant in the Pirtand / Parasnath belt).

Caste-vote association is reported as **ecological correlation at booth level**,
never as a statement about how any individual or community voted.
