# Runbook

Operational procedures for the Giridih AC-32 Election Monitor. Written for whoever is on call
during the by-election period.

## Daily rhythm

The worker container runs these on its own (Asia/Kolkata):

| Time | Job | If it fails |
|---|---|---|
| every 4h | `news.crawl` | Non-urgent. Check `news_source.last_error` for a dead feed. |
| 01:00 | `news.label_batch` | Articles stay unlabelled and are picked up tomorrow. No data loss. |
| 06:00 | `ceo.check_new_supplement` | Check the portal by hand; a new roll supplement is the one thing you do not want to miss. |
| 07:00 | `news.label_collect` | Batch results live 29 days, so a few failed runs are recoverable. |
| 07:15 | `news.embed` | Vector search degrades to text search until it succeeds. |
| 07:30 | `analytics.refresh` | **Urgent.** The dashboard is serving stale views until this runs. |
| 07:45 | `analytics.caste_estimate` | Non-urgent. |
| 03:00 | `ops.backup` | **Urgent.** See "Backups" below. |
| 03:30 | `ops.purge_prompts` | Non-urgent, but it is a compliance control — do not leave it failing. |
| 08:00 | `ops.usage_report` | Non-urgent. |

Check status at `/admin` → jobs, or:

```sql
SELECT DISTINCT ON (job) job, started, finished, status FROM job_run ORDER BY job, started DESC;
```

Run any job by hand:

```bash
docker compose exec worker python -m worker.run analytics.refresh
docker compose exec worker python -m worker.run --list
```

## After any data load

Always, in this order:

```bash
python -m analytics.refresh
python -m ingest.validate --strict --verbose
```

If validate fails, **do not publish the numbers**. The failures land in `review_queue` and appear
at `/admin` → review.

## The review queue

| Kind | What it means | What to do |
|---|---|---|
| `form20_row` | A row does not add up, or booth sums do not match the published AC total | Open the source PDF at the recorded page. Usually an OCR digit or a column shift. Correct and re-run the parser with `--replace`. |
| `crosswalk` | A polling station matched between 0.65 and 0.85, or not at all | Compare the building and village names. Fix with `POST /admin/crosswalk`, which marks it reviewed so the job will not overwrite you. |
| `ocr_page` | A page came back below the OCR confidence floor | Re-scan or transcribe by hand. Nothing from that page has loaded. |
| `roll_section` | A roll section's polling station has no booth | Usually a new station. Load the newer PS list first. |
| `area_alias` | A place name could not be matched to a panchayat or ward | Add the spelling to `area_alias`. These accumulate from news labelling and are worth clearing weekly. |
| `geocode` | A booth could not be placed | Pin it: `python -m ingest.geocode --pin B0042 --lat .. --lon ..` |

## Backups

`ops.backup` writes a gzipped `pg_dump` to the `backups` volume each night and prunes past
`BACKUP_RETENTION_DAYS`.

**Run the restore drill once before the ECI announcement.** A backup nobody has restored is not a
backup:

```bash
docker compose exec db createdb -U $POSTGRES_USER giridih_restore_test
gunzip -c /data/backups/giridih-<stamp>.sql.gz | \
  docker compose exec -T db psql -U $POSTGRES_USER giridih_restore_test
docker compose exec db psql -U $POSTGRES_USER giridih_restore_test \
  -c "SELECT count(*) FROM booth; SELECT count(*) FROM result_booth;"
docker compose exec db dropdb -U $POSTGRES_USER giridih_restore_test
```

Record the date of the drill. Copy backups off the host as well — a VPS failure takes the volume
with it.

## Cost control

`/admin` → usage. Two numbers matter:

- **Month to date** against `LLM_MONTHLY_CAP_USD`. At 80% the router stops using Sonnet 5 and every
  answer runs on Haiku; at 100% the chat goes read-only. The dashboard is never affected.
- **Cache hit rate**, which should be above 90%. If it falls, something volatile has entered the
  cached prompt prefix — a timestamp, a user id, a reordered tool list. The prefix is built in
  `chatbot/llm.py:cached_system_blocks()` and must be byte-identical between requests.

To raise one user's budget: `UPDATE app_user SET daily_token_budget = ... WHERE phone = ...`.

## Incidents

**The chat is failing but the dashboard is fine.** Expected behaviour under a budget cap or an
Anthropic outage. Check `/admin` → usage, then the API logs. No action needed on the data side.

**The dashboard shows stale numbers after a load.** `analytics.refresh` has not run or failed.
Run it by hand; it uses `REFRESH MATERIALIZED VIEW CONCURRENTLY`, so it is safe while serving.

**A booth's swing looks wrong.** Check its crosswalk confidence on the booth card. Below 0.85 and
unreviewed means the multi-year comparison is provisional — the card says so, and so should you.

**Someone asks the assistant for a voter's details.** It refuses locally, before any model call,
and there is nothing in the database to return. If you see a way to get an individual record out of
this system, treat it as a serious defect and stop using the affected path until it is fixed.

## Compliance checks

Run periodically and before any external sharing:

```bash
pytest tests/test_roll_privacy.py -q     # parsers discard names
python -m ingest.validate                 # schema has nowhere to put one
```

Raw roll PDFs are deleted after parsing unless `RETAIN_RAW_ROLLS=true`. Do not redistribute roll
extracts. Access is role-scoped: block users see only their block and the caste module is hidden
from them entirely.
