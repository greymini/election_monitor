---
slug: geography
topic: geography
title_en: How AC-32 is built up
title_hi: विधानसभा 32 की भौगोलिक संरचना
sources: HLD 3
last_reviewed: 2026-09-20
in_prompt: true
---

AC-32 Giridih (General seat), Giridih district, is the assembly segment of
PC-11 Giridih (whose other segments are Dumri, Gomia, Bermo, Tundi and
Baghmara).

Three administrative units make up the assembly seat:

- **Giridih Municipal Corporation** - 36 wards (urban)
- **Giridih Block** - 15 panchayats (rural)
- **Pirtand Block** - 17 panchayats (rural, includes the Parasnath / Madhuban belt)

The polling station is the atomic unit. Results, roll counts, new voters, caste
estimates and news tags all attach to a booth and roll up to panchayat or ward,
then to block, then to the AC.

Polling stations are renumbered, split and merged at every revision, so no
multi-year comparison is valid unless it goes through `booth_crosswalk`, which
maps a PS number in a given election to a stable `booth_uid`. Expect 10-20% of
booths to need manual matching; `booth_crosswalk.confidence` and `reviewed`
show how much weight a given comparison can bear.
