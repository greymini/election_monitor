# Jharkhand Election Monitor — Field Modules: High-Level Design

**Version:** 0.1 (draft for review) · **Date:** 19 Nov 2026 · **Extends:** HLD v2 (1 Oct 2026)
Covers two additions: **Expense Ledger** and **Ground Reports**, both usable from the dashboard and from a Telegram bot.

---

## 1. Purpose

The platform so far looks backwards at public documents. These two modules add what the candidate's own team knows and does:

| Module | Problem | What it gives |
|---|---|---|
| Expense Ledger | Money is spent daily by the candidate and by representatives across many small heads. Most of it is never written down, so nobody knows where it went, who spent it, or what it achieved. | One ledger of every rupee given and spent, classified into buckets, by area, by representative and over time, with what each spend reached. |
| Ground Reports | Representatives see incidents, grievances and openings first, but reporting is by phone calls and scattered WhatsApp messages that are lost. | A representative sends photos and a Hindi voice note to a bot. It is filed, routed to the right person, tracked to closure, and counted. |

The two meet in one question the candidate cannot answer today: **where is attention needed, and where is money actually going?**

## 2. Users and access

The existing roles stay. One role and one capability are added.

| Role | Expense Ledger | Ground Reports |
|---|---|---|
| admin (candidate / principal) | Everything, if finance scope is `all` | Everything |
| strategist | Nothing by default; summary only if granted | Everything except restricted categories unless granted |
| block | Nothing | Reports for own block |
| **rep** (new: field representative) | Own entries, own balance, own team's if they have one | Own reports and their status |

**Finance scope** is a separate capability (`none`, `own`, `team`, `all`), not tied to role. The technical admin who runs data loads does not automatically see money. Only the principal and a named treasurer hold `all`.

Representatives form a tree: candidate → area in-charges → workers. Each is attached to one or more blocks, panchayats or wards. That tree drives who can give money to whom, who is notified of what, and whose entries a person can see.

## 3. Principles (added to HLD v2 §4)

7. **Sending is enough.** A representative should never need more than one message and one tap. Anything not chosen is defaulted or left for review; nothing is lost because a form was not finished.
8. **Money given is not money spent.** An advance to a representative is a transfer, never an expense. Spend is counted once, when it leaves the team.
9. **The ledger is append-only.** Entries are voided with a reason, never edited or deleted. Every change records who and when.
10. **One ledger.** There is no second set of books. Statutory election accounts are a filtered view of the same entries.
11. **The recording is the record.** Voice transcripts and suggested categories are machine aids, labelled as such. The audio and the person's own choice always win.
12. **Impact is counted, not claimed.** The system records what a spend reached (people, events, issues closed). Comparisons with voting patterns are shown as association only.

## 4. Change to an existing principle: decision required

HLD v2 Principle 1 says no individual's data is stored anywhere. These modules cannot work under the strictest reading of that: a remark may name who was helped, a voice note will name people, a photo will show faces.

Proposed position, to be confirmed before build:

- Principle 1 stays absolute for **voter-roll data** (names, EPIC, addresses, phone numbers from rolls). Nothing changes there.
- Field data is a **separate privacy class** in its own schema. It never joins to roll data, never carries EPIC, Aadhaar or citizens' phone numbers (the PII screen still blocks those patterns), and is never exported in bulk without an audit entry.
- Beneficiaries are recorded as a **type and a count** by default ("medical help, 1 family, Pirtand"), not by name.
- Media is stored in a private bucket, served only through short-lived signed links, and never forwarded to group chats by the bot.

## 5. How it works

```
 Representative / candidate                       Candidate, coordinators, treasurer
   Telegram (text, photo, voice)                    Dashboard + Telegram alerts
            │                                                  ▲
            ▼                                                  │
   Bot webhook (FastAPI) ──► session state ──► PostgreSQL ─────┤
            │                                   (field schema) │
            ▼                                                  │
   Worker: media download, transcription,  ────────────────────┘
           classification, alerts, digests, follow-ups
```

**One bot, one rule for routing a message:**

| What arrives | What the bot does |
|---|---|
| A message starting with a number (`1500 chai nashta`) | Starts an expense entry |
| Photos, a voice note or a location | Starts or adds to a ground report |
| A button tap | Continues the open entry |
| Anything else | Shows the three-button menu: खर्च · रिपोर्ट · मेरा हिसाब |

### 5.1 Expense Ledger in brief

- **Three kinds of entry:** money given to a representative (advance), money spent (expense), money handed back (return). Money received from outside (party, supporters) can be logged but is optional.
- **Two confirmations for advances:** when the candidate logs "gave ₹50,000 to Ramesh", Ramesh's bot asks him to confirm receipt. Unconfirmed advances are flagged.
- **Running balance per representative:** advances received minus expenses and onward advances. A negative balance means the representative spent from their own pocket and is owed money.
- **Classification:** 12 preset buckets with sub-heads (LLD §4). The bot suggests a bucket from the words typed; the person confirms with one tap or picks another. "Other" asks for a short label; repeated labels are promoted to new sub-heads at a weekly review.
- **What the candidate sees:** spend by bucket, area, representative and week; the tail (how much money sits in small entries); balances held by each representative; areas with high need and low spend; and during an election, the running total against the statutory ceiling.

### 5.2 Ground Reports in brief

- **Intake:** photos and a Hindi voice note in any order, optionally a location. The bot groups them into one report, suggests a category and asks two things at most: category and whether it is urgent.
- **Nothing is lost:** an unfinished draft files itself after 10 minutes as "to be sorted".
- **Routing:** urgent reports go to the candidate and the coordinator at once with Acknowledge / Assign / Call buttons, and escalate if nobody acknowledges. Others go to the area in-charge and into an evening digest.
- **Closure:** every report has a status. The person who sent it is told when it is acknowledged and when it is closed. This is what keeps people reporting.
- **Issues:** several reports about the same thing attach to one issue with a timeline (uses the existing `public_issue` tables). Expenses can be linked to a report or issue, which gives cost per issue handled.
- **What the candidate sees:** an inbox to triage, a map of reports, issues by category and area, response times, and which representatives are active.

## 6. Legal and compliance position

This needs review by the candidate's election agent and a lawyer before go-live. This section is not legal advice; it lists the points the design is built around.

| Point | Design response |
|---|---|
| During an election, a candidate must keep a correct account of election expenditure and lodge it with the District Election Officer within 30 days of the result. The assembly ceiling for Jharkhand is ₹40 lakh. | Each AC has an election window (dates entered by admin). Entries in that window are tagged, mapped to statutory heads, totalled against the ceiling, and exportable as a register. |
| ECI instructions restrict cash payments above ₹10,000 for election expenses. | Cash entries above the configured limit in the window show a warning and are listed for the treasurer. |
| Giving money or gifts to electors to influence a vote is bribery under election law, and the model code of conduct restricts such assistance once it is in force. | Buckets for direct assistance and social gifts are marked sensitive. In the election window the bot warns before saving and the entry goes to the treasurer's review list. The system records; it does not decide legality. |
| The ledger is a complete financial record. If leaked or seized it is evidence. | Access by finance scope only; append-only audit; no bulk export without a logged reason; encryption at rest; bot chats cleaned after posting. |
| Photos and voice notes are personal data of the people in them. | Private storage, restricted categories, retention period set by the candidate, deletion on request. Check obligations under the DPDP Act with counsel. |

## 7. Architecture changes

| Area | HLD v2 | With field modules |
|---|---|---|
| Worker | Optional | **Required**: media download, transcription, alerts, escalation timers, digests |
| Inbound channel | None | Telegram webhook on the API, secret-verified, private chats only |
| Storage | Documents (local or S3) | Adds a private media bucket with signed URLs |
| Speech-to-text | None | Pluggable engine; self-hosted preferred so audio does not leave our servers (decision in §9) |
| LLM | Chatbot parked | Optional, off by default: tidy transcripts and suggest categories. Rules-only path always works. |
| Roles | admin, strategist, block | Adds `rep` and the finance-scope capability |

## 8. Phasing

| Phase | Delivers | Depends on |
|---|---|---|
| 1 | Team tree, Telegram linking, expense entry by bot and web, ledger, buckets, balances, basic summary | Worker running in the hosted setup |
| 2 | Ground report intake, inbox, urgent alerts with escalation, status feedback to reporter | Private media bucket |
| 3 | Transcription, evening digest, issue linking, report map, spend-versus-need view, impact follow-ups | Speech-to-text decision |
| 4 | Election-window register and ceiling tracking, optional LLM assist, voice-note expense entry | Legal review of statutory mapping |

## 9. Decisions needed before build

1. **Privacy carve-out** in §4: accept, or require stricter rules (for example, no names in remarks at all).
2. **Who holds finance scope `all`** besides the candidate.
3. **Speech-to-text:** self-hosted model, an external API, or none in the first release (people listen to the audio). Giridih speech carries Khortha; any engine must be tested on real samples before it is trusted.
4. **Approval or review:** are large entries by representatives blocked until approved, or posted and flagged? Draft assumes posted and flagged.
5. **Retention:** how long media and voided entries are kept.
6. **Telegram as the channel:** bot chats are not end-to-end encrypted and pass through Telegram's servers. Accept for this use, or restrict amounts above a threshold to the web form.

## 10. Risks

| Risk | Mitigation |
|---|---|
| Representatives stop logging after two weeks | One-message entry; evening nudge only to those holding an unspent advance; reporter always hears back |
| Advances counted as spend, inflating totals | Enforced in the single metric definition (Principle 8) with a test |
| "Other" swallows the tail | Weekly review promotes repeated labels; unclassified share shown on the summary with a target under 10% |
| Wrong transcript changes the meaning of a report | Audio is primary; transcript labelled machine-made with engine and confidence |
| Lost or shared phone | Link is per person, revocable instantly; money actions above a limit ask for a PIN |
| False or malicious reports | Reporter identity always attached; restricted categories; duplicate detection; nothing auto-published |
| Spend-versus-votes charts read as cause | Labelled association; never shown below area level; strategist-and-above only |

---

All figures, classifications and transcripts produced by these modules must be reviewed by a person before decisions rely on them.
