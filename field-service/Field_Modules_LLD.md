# Jharkhand Election Monitor — Field Modules: Low-Level Design

**Version:** 0.1 (draft for review) · **Date:** 19 Nov 2026 · **Extends:** LLD v2 (1 Oct 2026)
Read with `Field_Modules_HLD.md`. Section numbers here are local to this document.

**Assumptions to verify against the code before build**

- `ground_report`, `work_log`, `public_issue`, `issue_event`, `alert_rule`, `political_event` exist as empty tables (LLD v2 §3.6). Their columns were not available when this was written; §3.4 lists the columns needed and the migration should alter or replace accordingly.
- Latest migration is 0017; new ones start at 0018.
- `app_user` holds role and (AC, block) scope.
- Telegram Bot API behaviours quoted here (file size limits, message deletion rules, deep links) must be checked against the current Bot API documentation during build.

---

## 1. Components

| Component | Location | Responsibility |
|---|---|---|
| Bot webhook | `api/routers/telegram.py` | Verify, de-duplicate, hand the update to the dispatcher, return 200 fast |
| Dispatcher and flows | `field/bot/` (`dispatch.py`, `flow_expense.py`, `flow_advance.py`, `flow_report.py`, `flow_me.py`) | Message routing and the state machines |
| Parsers | `field/parse_amount.py`, `field/classify.py` | Amount extraction; bucket and category suggestion |
| Ledger service | `field/ledger.py` | The only code that writes `money_txn`; enforces invariants |
| Report service | `field/reports.py` | Draft assembly, filing, status changes, merge, issue linking |
| Media | `field/media.py` + `common/storage` | Download from Telegram, hash, store privately, signed URLs |
| Speech-to-text | `field/asr/` (`base.py`, one module per engine) | Pluggable transcription |
| Notifier | `field/notify.py` | Alerts, escalation, digests, reporter feedback |
| Worker jobs | `worker/jobs_field.py` | Queue consumers and schedules (§8) |
| Metrics | `analytics/metric_sql.py` (extended) | Spend and report metrics, same single-source rule |
| Web | `web/src/pages/field/` | Ledger, summary, balances, inbox, issues, team |

All new tables live in a `field` schema so the privacy scan and access rules can treat them as a class.

## 2. Team and Telegram linking

### 2.1 Tables

```
field.member(
  member_id        uuid PK,
  ac_id            FK ac,
  user_id          FK app_user UNIQUE,      -- every member is a platform user (role rep, block, strategist or admin)
  display_name     text,                    -- as the candidate knows them
  parent_member_id FK member NULL,          -- reports-to; NULL only for the principal
  is_principal     bool,                    -- exactly one per AC (partial unique index)
  finance_scope    text CHECK in (none, own, team, all),
  can_give_advance bool,
  active           bool,
  created_at, deactivated_at
)
field.member_scope(member_id, block_id NULL, area_id NULL)   -- one row per block or panchayat/ward handled
field.tg_link_token(token_hash PK, member_id, expires_at, used_at)
field.tg_link(telegram_user_id bigint PK, member_id UNIQUE, chat_id bigint, linked_at, revoked_at, pin_hash NULL)
field.tg_update(update_id bigint PK, received_at, outcome)   -- idempotency
field.tg_session(chat_id bigint PK, flow, state, payload jsonb, updated_at, expires_at)
```

### 2.2 Linking workflow

1. Admin adds the representative on the Team page (name, reports-to, areas, finance scope).
2. The platform creates a one-time token (valid 24 hours, stored hashed) and shows a link `https://t.me/<bot>?start=<token>` and a QR code.
3. The representative opens it; Telegram sends `/start <token>` to the bot.
4. The bot matches the token, writes `tg_link`, marks the token used, and replies with a welcome and the three-button keyboard.
5. Any message from a Telegram user with no active `tg_link` gets one fixed reply ("यह बॉट केवल पंजीकृत सदस्यों के लिए है") and nothing is stored beyond the update id.

Rules: private chats only (group updates are ignored); one Telegram account per member; unlinking on the Team page takes effect on the next update; deactivating a member revokes the link and freezes their balance for settlement.

### 2.3 Webhook

- `POST /telegram/webhook`, registered with a secret token; the request header is compared in constant time, mismatches get 403.
- Insert `update_id` into `tg_update`; on conflict return 200 and stop (Telegram retries deliveries).
- Text flows are handled in the request. Media download, transcription and alerts are queued for the worker so the webhook returns quickly.
- `tg_session` rows expire: 15 minutes for expense flows, 10 minutes for a report draft (which then auto-files, §5.2).

## 3. Data model

### 3.1 Expense buckets

```
field.exp_bucket(
  bucket_id PK, ac_id NULL,               -- NULL = shared preset
  parent_id NULL,                         -- two levels only
  code UNIQUE, name_hi, name_en,
  statutory_head NULL,                    -- mapping for the election register
  sensitive bool,                         -- warns in election window
  ask_reach bool,                         -- prompt for people reached
  active bool, sort int
)
field.exp_keyword(keyword, script, bucket_id, weight)       -- Devanagari and Roman spellings
field.exp_custom_label(ac_id, label_norm, count, last_seen, promoted_bucket_id NULL)
```

### 3.2 Ledger

```
field.money_txn(
  txn_id          uuid PK,
  txn_no          text UNIQUE,            -- human reference, e.g. 32-E-000412
  ac_id           FK ac,
  kind            text CHECK in (inflow, advance, expense, return, adjustment),
  amount          numeric(12,2) CHECK (amount > 0),
  txn_date        date,                   -- when it happened; defaults to today
  from_member_id  FK member NULL,
  to_member_id    FK member NULL,
  bucket_id       FK exp_bucket NULL,     -- expense only
  custom_label    text NULL,              -- when bucket is OTHER
  block_id NULL, area_id NULL,
  fund_source     text NULL,              -- §4.3
  pay_mode        text NULL,              -- §4.4
  payee_kind      text NULL,              -- vendor | individual | institution | group
  payee_label     text NULL,              -- shop or institution name; PII-screened
  reach_count     int NULL,               -- people or families reached
  reach_unit      text NULL,              -- person | family | village | event
  remark          text NULL,              -- PII-screened
  receipt_asset_id FK media_asset NULL,
  report_id NULL, issue_id NULL, political_event_id NULL,   -- links
  in_election_window bool,                -- set at insert from ac_election_window
  counts_to_ceiling  bool NULL,           -- set by treasurer; NULL = not yet decided
  suggest_source  text,                   -- user | rule | llm
  suggest_conf    numeric(4,3) NULL,
  status          text CHECK in (posted, pending_ack, disputed, void),
  flagged         text[] ,                -- large | cash_over_limit | sensitive_in_window | backdated
  voids_txn_id NULL, void_reason NULL, voided_by NULL, voided_at NULL,
  created_by FK member, created_via text CHECK in (telegram, web), created_at
)
field.ac_election_window(ac_id, event_id, starts_on, ends_on, ceiling_amount, cash_limit)
field.txn_followup(txn_id, due_on, asked_at, outcome NULL, note NULL, asset_id NULL)   -- impact follow-up
```

**Invariants (enforced in `field/ledger.py` and by CHECK constraints)**

| Kind | from | to | bucket | Meaning |
|---|---|---|---|---|
| expense | member | NULL | required | Money left the team |
| advance | member | member (different) | NULL | Money moved inside the team; starts `pending_ack` |
| return | member | member (their giver) | NULL | Unspent money handed back |
| inflow | NULL | member | NULL | Money received from outside; `fund_source` required |
| adjustment | either | either | NULL | Treasurer correction with mandatory remark |

- No UPDATE on amount, kind, members, date or bucket after insert. The creator may undo within 10 minutes (hard delete of their own just-created row is not allowed either: undo is a void with reason "undo"). Bucket re-classification is the one permitted change, written to `field.txn_audit`.
- A void creates no negative row; it sets `status = void` and all metrics exclude void.
- An advance may only go to someone in the giver's subtree, unless the giver is the principal or treasurer.
- `txn_date` more than 7 days in the past adds the `backdated` flag.

### 3.3 Balance

```
balance(member) =  Σ inflow to member
                 + Σ advance to member   (status posted)
                 + Σ return to member
                 − Σ advance from member (status posted or pending_ack)
                 − Σ return from member
                 − Σ expense from member
```

A pending advance is deducted from the giver immediately but not credited to the receiver until acknowledged; the difference is reported as "in transit". The principal's balance is not shown (they are the source); for everyone else a negative balance means "owed to this person".

### 3.4 Ground reports

```
field.report_category(category_id PK, code, name_hi, name_en, default_urgency, restricted bool, sort, active)
field.report_keyword(keyword, script, category_id, urgency_hint NULL, weight)

ground_report(                         -- extend the existing table
  report_id uuid PK, report_no text UNIQUE,      -- e.g. 32-R-000087
  ac_id, reporter_member_id,
  block_id NULL, area_id NULL, booth_uid NULL,
  lat NULL, lon NULL, loc_source text,           -- shared | area_default | none
  category_id NULL, urgency text CHECK in (normal, urgent),
  title NULL,                                    -- first sentence of transcript, editable
  note_text NULL,                                -- anything typed
  transcript_hi NULL, transcript_status text,    -- none | queued | done | failed
  transcript_engine NULL, transcript_conf NULL,
  summary NULL, summary_source NULL,             -- human | llm
  status text CHECK in (draft, new, acknowledged, assigned, in_progress, resolved, closed_no_action, duplicate),
  assigned_to NULL, ack_by NULL, ack_at NULL, resolved_at NULL, resolution_note NULL,
  duplicate_of NULL, issue_id FK public_issue NULL,
  occurred_at NULL, created_via, created_at, filed_at
)
field.media_asset(asset_id uuid PK, sha256, kind CHECK in (photo, voice, video, document),
                  storage_key, bytes, mime, duration_s NULL,
                  tg_file_unique_id, uploaded_by, created_at, delete_after NULL)
field.report_media(report_id, asset_id, seq)
field.report_event(report_id, at, actor_member_id NULL, kind, note)   -- status changes, notifications, escalations
```

`public_issue` and `issue_event` are used as they stand: an issue is the long-running thing ("Madhuban water supply"), a report is one observation of it.

### 3.5 Views

| View | Grain | Used by |
|---|---|---|
| `field.mv_spend_period` | ac, week, bucket, sub-bucket, block, area, member | Summary charts |
| `field.mv_member_balance` | ac, member | Balances page, bot "मेरा हिसाब" |
| `field.mv_spend_tail` | ac, month, size band | Tail analysis |
| `field.mv_report_area` | ac, week, block, area, category, status | Report heat and trends |
| `field.mv_need_vs_spend` | ac, area | Reports opened, issues open, spend, electors, priority score |

Refreshed by the worker every 15 minutes and after any void.

## 4. Presets

### 4.1 Expense buckets

Sub-heads are the options shown after a bucket is chosen; they are optional to pick. "S" marks buckets flagged sensitive in an election window.

| # | Code | Bucket (EN) | Bucket (HI) | Sub-heads | S |
|---|---|---|---|---|---|
| 1 | EVENT | Meetings and events | सभा और कार्यक्रम | Tent and stage · Sound and lights · Chairs and venue · Event food · Permissions and fees | |
| 2 | TRAVEL | Travel and vehicles | यात्रा और वाहन | Fuel · Vehicle hire · Driver · Repair · Outstation travel and stay | |
| 3 | FOOD | Food and hospitality | भोजन और नाश्ता | Tea and snacks · Worker meals · Guests and visiting leaders | |
| 4 | TEAM | Workers and team | कार्यकर्ता खर्च | Daily allowance · Phone recharge · Lodging · Booth-level team | |
| 5 | PUBLICITY | Publicity and media | प्रचार और मीडिया | Banners and flex · Printing · Wall writing · Social media and ads · Photo and video · Newspaper and local channels | |
| 6 | OFFICE | Office and operations | कार्यालय खर्च | Rent · Electricity and internet · Stationery · Equipment · Staff | |
| 7 | SOCIAL | Social occasions | सामाजिक अवसर | Wedding · Bereavement and shraddh · Festival and puja committee · Community feast | S |
| 8 | ASSIST | Public assistance | जन सहायता | Medical · Education · Funeral · Accident and disaster relief · Livelihood · Other individual help | S |
| 9 | DONATE | Donations to institutions | संस्थागत दान | Religious place · Club and youth group · School · Sports tournament · Cultural programme | S |
| 10 | WORKS | Small local works | छोटे विकास कार्य | Handpump and water · Street light · Road and drain patch · Community building repair | S |
| 11 | PARTY | Party and organisation | संगठन खर्च | Party programme · Membership drive · Committee meetings · Contribution to party | |
| 12 | OTHER | Other | अन्य | Free-text label (max 40 characters) | |

Statutory heads for the election register (public meetings and rallies, campaign material, media, vehicles, workers and agents, and so on) are stored in `statutory_head`. The exact mapping must be set by the candidate's election agent against the current ECI register format; the seed file ships with the column empty.

### 4.2 Ground report categories

"U" = urgent by default. "R" = restricted (visible to principal, coordinator and reporter only unless shared).

| # | Code | Category (EN) | Category (HI) | Typical content | U | R |
|---|---|---|---|---|---|---|
| 1 | ACCIDENT | Accident, fire, disaster | दुर्घटना / आपदा | Road accident, fire, house collapse, storm damage | U | |
| 2 | HEALTH | Health | स्वास्थ्य | Medical emergency, hospital refusing care, outbreak, ambulance | U | |
| 3 | LAW | Law and order | कानून-व्यवस्था | Violence, dispute, tension between groups, police matter | U | R |
| 4 | DEATH | Death and bereavement | मृत्यु / शोक | Death in a known family; visit or condolence needed | U | |
| 5 | SERVICES | Electricity, water, roads | बिजली-पानी-सड़क | Outage, dry handpump, broken road, drainage | | |
| 6 | SCHEME | Government schemes | सरकारी योजना | Pension, ration, housing, certificates, benefit not received | | |
| 7 | CORRUPT | Corruption and misconduct | भ्रष्टाचार | Bribe demanded, funds misused, official misconduct | | R |
| 8 | LAND | Land, mining, displacement | जमीन / खनन / विस्थापन | Land dispute, compensation, mining impact | | |
| 9 | EDU | Education | शिक्षा | School without teachers, midday meal, scholarship | | |
| 10 | WORK | Employment and migration | रोजगार / पलायन | Wages unpaid, job loss, migrant worker in trouble | | |
| 11 | FARM | Farming and forest | खेती / सिंचाई / वन | Crop loss, irrigation, forest rights, animal damage | | |
| 12 | SOCIALEV | Social event or invitation | सामाजिक कार्यक्रम | Wedding, festival, tournament where presence matters | | |
| 13 | POLITICAL | Political activity | राजनीतिक गतिविधि | Other parties' events, local mood, people joining or leaving | | R |
| 14 | HELPREQ | Request for help | सहायता अनुरोध | A person or family asking for assistance | | |
| 15 | OTHER | Other | अन्य | Anything else | | |

### 4.3 Fund source

`own` (personal) · `family_business` · `party` · `supporter_cash` · `supporter_in_kind` · `advance` (default for a representative spending from money given) · `own_pocket_reimbursable` · `other`.
In-kind support (a supporter pays the tent vendor directly) is entered as an expense with source `supporter_in_kind` so the full cost of an activity is visible.

### 4.4 Payment mode

`cash` (default) · `upi` · `bank_transfer` · `cheque` · `credit` (to be paid later; listed as outstanding) · `in_kind`.

### 4.5 Defaults and limits (configurable per AC)

| Setting | Default |
|---|---|
| Large-entry flag | ₹25,000 |
| Tail threshold | ₹2,000 |
| Advance acknowledgement reminder | 24 hours, then flagged at 48 |
| PIN required above | ₹50,000 by bot |
| Urgent report acknowledgement | 30 minutes, then escalate |
| Report draft auto-file | 10 minutes |
| Impact follow-up (ASSIST, WORKS) | 14 days after entry |
| Evening digest | 20:00 IST |

## 5. Bot workflows

All bot text is Hindi. Buttons are inline keyboards. Amounts display with Indian grouping.

### 5.1 Expense

**Amount parser** (`field/parse_amount.py`): takes the first number in the message; accepts ASCII and Devanagari digits, commas, `k`, `हजार / hazar / hajar`, `लाख / lakh / lac`, decimals with multipliers (`2.5 lakh`). The rest of the message is the description. Rejects zero, negatives and anything above a sanity limit (asks to confirm).

**Suggestion** (`field/classify.py`): normalise the description (existing `common` text normalisation, transliteration-insensitive), score against `exp_keyword`, take the top bucket if its score clears a threshold and leads the second by a margin; otherwise ask. Area defaults to the member's only scope, or the area they used last.

**Fast path (one message, one tap)**

```
Rep:  1500 chai nashta baithak
Bot:  ₹1,500 · भोजन और नाश्ता · पीरटांड़ · आज · नकद
      [ सही है, दर्ज करें ]
      [ श्रेणी बदलें ] [ क्षेत्र बदलें ]
      [ और विवरण ]   [ रद्द करें ]
Rep:  (taps सही है)
Bot:  दर्ज हुआ: 32-E-000412
      आपके पास शेष: ₹8,500     [ गलती हुई, हटाएँ ]
```

**Amount only**

```
Rep:  1500
Bot:  ₹1,500 किस पर खर्च हुआ?
      (the member's six most-used buckets, then [ और श्रेणियाँ ] and [ अन्य ])
Rep:  (taps a bucket)   → same confirmation card as above
```

**"अन्य"** → "क्या था? छोटे में लिखें" → stored as `custom_label`, bucket OTHER, counted in `exp_custom_label`.

**"और विवरण"** opens, one question at a time, each skippable: sub-head · date (आज / कल / तारीख लिखें) · payment mode · paid from (advance / own pocket) · people reached (for buckets with `ask_reach`) · receipt photo · remark.

**State machine:** `idle → have_amount → have_bucket → confirm → posted`, with `details_*` sub-states and `cancelled`. A photo sent while the state is `details_receipt` attaches to the expense; a photo in any other state starts a report (§5.3).

**Warnings before saving** (still one extra tap, never a block): sensitive bucket inside the election window; cash above the window's cash limit; amount above the large-entry flag. The entry is saved with the matching `flagged` value.

### 5.2 Advance, acknowledgement, return

```
Candidate:  (taps पैसा दिया)                     or types:  दिया 50000 रमेश
Bot:        किसे दिया?   (list of direct reports, most recent first)
Candidate:  (taps रमेश महतो)
Bot:        कितना?
Candidate:  50000
Bot:        ₹50,000 → रमेश महतो · आज · नकद      [ दर्ज करें ] [ किस काम के लिए (वैकल्पिक) ] [ रद्द ]

   ... to Ramesh:
Bot:        <नाम> ने दर्ज किया है कि आपको ₹50,000 दिया गया।
            [ हाँ, मिला ]   [ रकम अलग है ]   [ नहीं मिला ]
```

- "हाँ, मिला" → status `posted`; both sides see updated balances.
- "रकम अलग है" or "नहीं मिला" → status `disputed`; giver and treasurer are told; resolved on the web by voiding and re-entering.
- No reply in 24 hours → reminder; 48 hours → flagged on the Balances page.
- A representative with `can_give_advance` uses the same flow to pass money down their own subtree.
- **Return:** "मेरा हिसाब" → [ पैसा लौटाया ] → amount → the giver confirms in the same way.

### 5.3 Ground report

**Draft assembly:** the first photo, voice note, video or location from a member with no open expense state opens a draft. Everything sent in the next 3 minutes (rolling) joins it; photos sent as an album share Telegram's media-group id and are kept together. Typed text during a draft becomes `note_text`.

```
Rep:  (sends 3 photos and a 40-second voice note)
Bot:  रिपोर्ट मिली: 3 फोटो, 1 आवाज़ संदेश · पीरटांड़
      यह किस बारे में है?
      (suggested category first if there is one, then the rest in two columns)
Rep:  (taps बिजली-पानी-सड़क)
Bot:  क्या इस पर तुरंत ध्यान चाहिए?
      [ हाँ, तुरंत ]   [ सामान्य ]
Rep:  (taps सामान्य)
Bot:  दर्ज हुई: 32-R-000087
      [ जगह बदलें ]  [ लोकेशन भेजें ]  [ और जोड़ें ]
```

- **If the representative walks away:** after 10 minutes the draft files itself with category NULL ("छाँटना बाकी") and urgency from keywords in any typed text, else normal. It appears at the top of the inbox for triage.
- **Urgent shortcut:** a message or caption beginning with `तुरंत` or `urgent` files the report as urgent immediately and asks the category afterwards.
- **Location:** shared location wins; otherwise the member's scope area; a booth can be attached later on the web.
- **Limits:** up to 10 photos, voice up to 5 minutes, video up to the Bot API download limit; larger files get a reply asking for a shorter clip.

**Processing after filing (worker)**

1. Download each file through the Bot API, compute sha256, store in the private media bucket, write `media_asset`.
2. Queue the voice note for transcription (§6). Filing and alerts never wait for it.
3. Look for likely duplicates: same area and category within 24 hours; show "possibly the same as 32-R-000081" in the inbox with a merge button.
4. Notify (§5.4) and write `report_event` rows.

**Status lifecycle**

```
draft → new → acknowledged → assigned → in_progress → resolved
                    │                          └────→ closed_no_action
                    └────────────────────────────────→ duplicate (merged into another)
```

The reporter receives a message at acknowledged ("देख लिया गया है") and at any closing status with the resolution note. Reopening is allowed by the principal or coordinator and is logged.

### 5.4 Alerts, escalation, digest

| Trigger | Who is told | How |
|---|---|---|
| Urgent report filed | Principal, coordinator, reporter's parent | Immediately: category, area, reporter, first photo, transcript when ready; buttons [ देख लिया ] [ सौंपें ] [ रिपोर्टर को कॉल ] |
| Urgent, no acknowledgement in 30 minutes | Same people again, plus next level up | Repeat with "30 मिनट से लंबित"; again at 60 minutes |
| Normal report filed | Reporter's parent (area in-charge) | Single message, no escalation |
| Restricted category | Principal and coordinator only | Text only; media viewed on the dashboard |
| 20:00 daily | Principal, coordinator, treasurer (finance part only to finance scope) | Digest: reports today by category and area, open urgent items, spend today by bucket, advances unconfirmed |
| Monday 09:00 | Principal | Week summary and the "need versus spend" top five areas |
| Advance held and no expense logged for 5 days | That representative | One nudge: "आपके पास ₹X शेष है; कोई खर्च दर्ज करना है?" |

"सौंपें" lists members for that area; the assignee gets the report with [ काम शुरू ] [ पूरा हुआ ] buttons and may attach a closing photo. `alert_rule` stores these rules so they are data, not code.

### 5.5 "मेरा हिसाब"

Shows balance, money in transit, last five entries, open reports and their status, and buttons [ पैसा लौटाया ] [ पिछला महीना ]. For members with finance scope `team` it adds each direct report's balance.

## 6. Speech-to-text and optional LLM

- Interface: `transcribe(audio_path, lang='hi') → (text, confidence, engine)`. One module per engine; chosen by `ASR_ENGINE` (`none` is valid and is the default until §9 decision 3 of the HLD is made).
- Candidates to evaluate on at least 50 real voice notes from Giridih representatives: a self-hosted open-source multilingual model, an Indian-language model, and a hosted API. Measure word error rate and, more usefully, whether a coordinator can triage correctly from the transcript alone. Khortha-influenced speech is the expected weak point.
- Audio is converted to 16 kHz mono before transcription; the original file is always kept.
- The transcript is shown under the audio player with the label "मशीन द्वारा लिखा गया" and the engine name. Coordinators can correct it; corrections are kept as the human transcript and feed the evaluation set.
- Keyword rules run on the transcript to suggest a category and urgency for auto-filed drafts. A suggestion never overrides a choice the reporter made.
- LLM (`FIELD_LLM_ENABLED=false` by default): when on, produces a two-line summary and a category suggestion from the transcript, and may suggest a bucket when keyword rules fail. Uses the parked chatbot's client and the existing monthly cap; names and numbers are not redacted from the transcript before the call, so turning this on is a data-sharing decision, not a technical one.

## 7. Metrics

Added to `analytics/metric_sql.py`, with the existing Python/SQL parity test extended. All exclude `void`; all NULL rules follow `docs/METRICS.md`.

| Metric | Definition |
|---|---|
| spend_total | Σ amount where kind = expense. Advances, returns and inflows never count. |
| spend_share_pct | bucket spend ÷ spend_total for the same filter |
| member_balance | §3.3 |
| in_transit | Σ advances with status pending_ack |
| unclassified_pct | spend in OTHER ÷ spend_total |
| tail_count_pct / tail_value_pct | entries with amount ≤ tail threshold, as a share of entries and of value |
| top_decile_value_pct | value in the largest 10% of entries ÷ spend_total |
| logging_lag_days | median(created_at − txn_date) per member |
| spend_per_1000_electors | area spend ÷ area electors × 1000; NULL when no roll snapshot is linked |
| reach_total / cost_per_reach | Σ reach_count; spend ÷ reach, only over entries where reach is filled, with coverage % shown beside it |
| ceiling_used_pct | Σ expense where in_election_window and counts_to_ceiling is not false ÷ ceiling_amount |
| reports_open / reports_per_1000_electors | by area and category |
| ack_minutes / resolve_days | median time from filed to acknowledged; filed to resolved |
| member_active_days | days in the last 14 with any entry or report |
| need_vs_spend_rank_gap | percentile rank of open reports per 1000 electors minus percentile rank of spend per 1000 electors, per area; large positive = attention needed and little spent |

**Impact, in three layers**

1. **At entry:** people reached and a link to the event, report or issue the spend was for.
2. **Follow-up:** for ASSIST and WORKS, the bot asks the same member after 14 days "काम पूरा हुआ?" with [ हाँ ] [ आंशिक ] [ नहीं ] and an optional photo; stored in `txn_followup`.
3. **Area view:** spend and reach per area beside open issues, priority score and, after an election, the swing. Labelled "association, not cause"; shown only at panchayat/ward level and above; strategist-and-above with finance scope.

## 8. Worker jobs

| Job | Trigger | Action |
|---|---|---|
| `media_fetch` | queue | Download, hash, store, link |
| `transcribe` | queue | Run the configured engine; write transcript; re-run keyword suggestion |
| `report_autofile` | every minute | File drafts older than 10 minutes |
| `urgent_escalate` | every 5 minutes | Re-alert unacknowledged urgent reports |
| `advance_remind` | hourly | Remind and flag unacknowledged advances |
| `followup_ask` | daily 10:00 | Send due impact follow-ups |
| `digest_daily`, `digest_weekly` | schedule | Compose and send |
| `label_review` | weekly | List custom labels seen three or more times for promotion |
| `field_refresh` | every 15 minutes | Refresh field views |
| `chat_cleanup` | hourly | Delete the bot's own confirmation cards older than the configured age, where the Bot API allows |
| `media_retention` | daily | Remove assets past `delete_after` |

Each run writes to the existing `job_run` table. The queue is a Postgres table polled with `FOR UPDATE SKIP LOCKED`; no new infrastructure.

## 9. API

All under `/acs/{ac_number}/field/…` except the webhook. Role and scope are applied in the query, as elsewhere.

| Route | Purpose | Access |
|---|---|---|
| `POST /telegram/webhook` | Bot updates | Telegram secret header |
| `GET/POST/PATCH /members` | Team tree and scopes | admin |
| `POST /members/{id}/link-token`, `DELETE /members/{id}/link` | Telegram link and unlink | admin |
| `GET /buckets`, `POST/PATCH /buckets` | Presets and AC-specific additions | read: finance scope ≥ own; write: admin |
| `GET /txns` | Ledger with filters (date, kind, bucket, area, member, flag, status) | finance scope; rows limited to own, team or all |
| `POST /txns` | Create expense, advance, return, inflow | finance scope ≥ own |
| `POST /txns/{id}/ack`, `/dispute`, `/void`, `/reclassify`, `/ceiling` | State changes | receiver; creator within 10 minutes; treasurer |
| `GET /spend/summary?group_by=bucket,area,member,week` | Aggregates | finance scope team or all |
| `GET /spend/tail`, `GET /balances`, `GET /spend/need-vs-spend` | Analysis | finance scope all (balances: team sees own subtree) |
| `GET /spend/register?window=…` | Election-window register export | finance scope all; logged with reason |
| `GET /reports` | Inbox with filters | admin, strategist; block and rep scoped; restricted categories filtered |
| `POST /reports` | Create from web | any member |
| `GET /reports/{id}`, `GET /reports/{id}/media/{asset_id}` | Detail; signed URL valid 5 minutes | as above |
| `POST /reports/{id}/ack`, `/assign`, `/status`, `/merge`, `/link-issue`, `/transcript` | Triage actions | admin, strategist, assigned member |
| `GET /reports/summary` | Counts by area, category, status, week | admin, strategist |

The route × role contract test (LLD v2 §11) is extended with the `rep` role and with finance scope as a second dimension.

## 10. Frontend

New navigation group **Field / मैदान**.

| Page | Content |
|---|---|
| Spend summary | Cards: spend this month, in transit, held by team, unclassified %, ceiling used (in window). Charts: bucket share, weekly trend stacked by bucket, by area (map and table), by representative. Tail panel: count versus value by size band. |
| Ledger | Sortable, filterable table of every entry with flags, receipt thumbnail, links to report or issue; void and re-classify actions; CSV export (logged). |
| Team balances | Tree view: advances given, spent, returned, balance, in transit, last entry date, logging lag. Disputed and unacknowledged advances at the top. |
| Add entry | Web form equivalent to the bot flow, for the candidate's office to enter in bulk. |
| Buckets | Presets, AC-specific sub-heads, keyword lists, custom labels awaiting promotion. |
| Reports inbox | Queue ordered urgent first, then unsorted, then oldest; each card has photos, audio player, transcript, reporter, area, age; one-click acknowledge, assign, merge, link to issue. |
| Reports map | Pins by category and status on the existing Leaflet map; filter by week. |
| Issues | Issue list with timeline of reports, actions, linked spend and linked news items. |
| Need versus spend | Area table and scatter with the association label. |
| Team | Members, reports-to, areas, finance scope, Telegram link status, last active. |

Existing rules apply: Hindi default, provenance on every figure ("entered by … via Telegram"), NULL as "—" with a reason, estimates and machine output labelled.

## 11. Security and privacy

- **Identity:** a Telegram user is trusted only through an active `tg_link`. Tokens are single-use, hashed, short-lived.
- **PIN:** set at linking; asked for any expense or advance above the PIN limit (§4.5). Stored as a bcrypt hash; three failures lock bot money actions until reset on the web.
- **Authorisation:** finance scope and subtree checks in the query; restricted report categories filtered in the query, not the UI.
- **PII screen:** existing screen runs on `remark`, `payee_label`, `custom_label`, `note_text` and transcripts. EPIC, Aadhaar and mobile-number patterns are rejected in typed fields and masked in transcripts.
- **Privacy scan:** `ingest.validate --privacy` is extended to the `field` schema with its own rule set (names permitted in transcripts and remarks if HLD §4 is accepted; identifiers never).
- **Media:** private bucket; signed URLs of 5 minutes; no public links; the bot forwards at most one photo in an alert and none for restricted categories; `delete_after` set from the AC retention setting.
- **Audit:** `field.txn_audit` and `report_event` record every state change, export and media view by a non-owner.
- **Bot hygiene:** confirmation cards are removed after a configurable period so a borrowed phone does not show history; "मेरा हिसाब" always reads live from the database.
- **Secrets:** bot token and webhook secret from environment only; preflight refuses to start with placeholders, as for `JWT_SECRET`.
- **Isolation from roll data:** no foreign key or view joins the `field` schema to any roll table other than through area-level elector counts.

## 12. Migrations

| No. | Content |
|---|---|
| 0018 | `field` schema; `member`, `member_scope`; role `rep`; finance scope |
| 0019 | Telegram tables (`tg_link_token`, `tg_link`, `tg_update`, `tg_session`); job queue table |
| 0020 | `exp_bucket`, `exp_keyword`, `exp_custom_label`, `ac_election_window`; seeds from §4.1, §4.3, §4.4 |
| 0021 | `money_txn`, `txn_audit`, `txn_followup`; constraints and indexes on (ac_id, txn_date), (from_member_id), (to_member_id), (bucket_id) |
| 0022 | `report_category`, `report_keyword` with seeds from §4.2; alter `ground_report`; `media_asset`, `report_media`, `report_event` |
| 0023 | Field materialized views and generated metric functions |

## 13. Testing

| Layer | Cases |
|---|---|
| Unit | Amount parser (digits in both scripts, multipliers, commas, rejects); classifier thresholds; ledger invariants per kind; balance arithmetic including pending and disputed advances; void exclusion; subtree rule for advances |
| Metric parity | Every metric in §7, Python against generated SQL; "advance is never spend" as a named test |
| Bot flows | Recorded Telegram updates replayed through the dispatcher: fast path, amount only, other label, details path, cancel, timeout, duplicate update id, unlinked user, group chat ignored |
| Reports | Album grouping; rolling draft window; auto-file at 10 minutes; urgent shortcut; duplicate suggestion; status transitions and reporter feedback; restricted category visibility |
| Security | Route × role × finance-scope contract; token reuse and expiry; PIN lockout; signed URL expiry; PII patterns rejected or masked |
| End to end | Embedded Postgres with a fake Telegram server: candidate gives advance → representative acknowledges → logs three expenses → returns the rest → balances and summary reconcile to zero |
| Field trial | Five representatives for one week before wider use; measure entries per person per day, share completed by fast path, auto-filed share of reports, transcript usefulness |

## 14. Open items

1. Decisions listed in HLD §9.
2. Statutory head mapping and the register format, from the election agent.
3. Real column list of the existing extended tables, to settle alter-versus-replace in migration 0022.
4. Keyword seed lists for buckets and categories in Devanagari and Roman spellings, to be built from the first two weeks of real entries.
5. Whether the hosted setup (Railway) runs transcription itself or a separate small machine does.

---

All figures, classifications and transcripts produced by these modules must be reviewed by a person before decisions rely on them.
