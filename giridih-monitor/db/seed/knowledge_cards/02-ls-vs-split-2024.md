---
slug: ls-vs-split-2024
topic: transfer
title_en: The 2024 Lok Sabha / Vidhan Sabha split
title_hi: 2024 लोकसभा बनाम विधानसभा का अंतर
sources: HLD 1.1 | HLD 7 module 6
last_reviewed: 2026-09-20
in_prompt: true
---

In Lok Sabha 2024 (PC-11 Giridih): AJSU (C.P. Choudhary) 4,51,139 / 35.7%;
JMM (Mathura Mahato) 3,70,259 / 29.3%; JLKM (Jairam Mahato) 3,47,322 / 27.5%.

**AJSU led in the Giridih assembly segment in the Lok Sabha poll, yet JMM held
the seat in the assembly poll six months later.** This LS-to-VS split is the
single most important dynamic to model for the bypoll.

Where to look in the data:

- `mv_transfer_ls_vs` - per booth, LS-2024 against VS-2024 share for each party.
- `mv_floating_vote` - Pedersen index per booth; high values mark booths that
  genuinely voted differently in the two polls.
- JLKM polled about 27% across the parliamentary seat but about 5% in the
  assembly seat. Where that vote sat in each poll, booth by booth, is the
  question a bypoll projection turns on.

Mind the unit mismatch: the Lok Sabha percentages above are for the whole
parliamentary constituency (six segments), not for AC-32 alone. Segment-level
Form 20 is what the transfer views actually use.
