"""Operational jobs: backup, prompt purge, usage report, supplement watch."""

from __future__ import annotations

import gzip
import json
import shutil
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path

from common.config import get_settings
from common.db import execute, query, query_one
from common.logging_setup import get_logger

log = get_logger(__name__)


def backup() -> dict:
    """Nightly pg_dump, gzipped, with retention (LLD 12).

    A backup nobody has restored is not a backup: run the restore drill once
    before the ECI announcement, as the LLD requires.
    """
    settings = get_settings()
    target_dir = Path(settings.backup_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    path = target_dir / f"giridih-{stamp}.sql.gz"

    if shutil.which("pg_dump") is None:
        raise RuntimeError("pg_dump not found - it ships in the worker image (postgresql-client)")

    dump = subprocess.run(
        ["pg_dump", "--no-owner", "--no-acl", settings.database_url],
        capture_output=True, timeout=1800,
    )
    if dump.returncode != 0:
        raise RuntimeError(f"pg_dump failed: {dump.stderr.decode(errors='replace')[:500]}")

    with gzip.open(path, "wb") as fh:
        fh.write(dump.stdout)

    cutoff = date.today() - timedelta(days=settings.backup_retention_days)
    removed = 0
    for old in target_dir.glob("giridih-*.sql.gz"):
        if date.fromtimestamp(old.stat().st_mtime) < cutoff:
            old.unlink()
            removed += 1

    return {"file": path.name, "bytes": path.stat().st_size, "pruned": removed}


def purge_prompts() -> dict:
    """Drop prompt bodies older than PROMPT_RETENTION_DAYS (LLD 12).

    Token counts and costs in llm_usage are kept - they are the audit trail.
    Only the prompt text goes.
    """
    days = get_settings().prompt_retention_days
    n = execute("DELETE FROM llm_prompt_log WHERE ts < now() - make_interval(days => %s)", (days,))
    return {"deleted": n, "retention_days": days}


def usage_report() -> dict:
    """Daily spend summary for the admin (LLD 7)."""
    settings = get_settings()
    today = query_one(
        "SELECT COALESCE(SUM(cost_usd), 0) AS cost, COUNT(*) AS calls "
        "FROM llm_usage WHERE ts::date = CURRENT_DATE - 1"
    ) or {"cost": 0, "calls": 0}
    month = query_one(
        "SELECT COALESCE(SUM(cost_usd), 0) AS cost FROM llm_usage "
        "WHERE date_trunc('month', ts) = date_trunc('month', now())"
    ) or {"cost": 0}
    by_purpose = query(
        "SELECT purpose, model, ROUND(SUM(cost_usd), 4) AS cost, COUNT(*) AS calls "
        "FROM llm_usage WHERE ts::date = CURRENT_DATE - 1 "
        "GROUP BY purpose, model ORDER BY cost DESC"
    )

    cap = settings.monthly_cap_usd
    spend = float(month["cost"] or 0)
    pct = 100.0 * spend / cap if cap else 0.0
    report = {
        "yesterday_usd": round(float(today["cost"] or 0), 4),
        "yesterday_calls": today["calls"],
        "month_to_date_usd": round(spend, 4),
        "monthly_cap_usd": cap,
        "pct_of_cap": round(pct, 1),
        "by_purpose": by_purpose,
    }
    if pct >= settings.degrade_at_pct:
        log.warning("LLM spend is at %.0f%% of the monthly cap - analysis has been "
                    "downgraded to Haiku", pct)
    return report


def check_new_supplement() -> dict:
    """Watch the CEO portal for a new roll supplement (LLD 7).

    Discovery does not download, so there is no content hash to compare against -
    identity here is the URL. A supplement is "new" when its URL is neither in
    source_doc nor already sitting in the review queue. Parsing stays a
    deliberate manual step: a roll load is not something to run unattended.
    """
    from ingest.fetch_ceo import list_remote_documents

    found = list_remote_documents(kind="roll_supplement")
    known_urls = {
        r["url"] for r in query("SELECT url FROM source_doc WHERE url IS NOT NULL")
    }
    already_flagged = {
        r["ref"] for r in query(
            "SELECT ref FROM review_queue WHERE kind = 'roll_section' AND status = 'open'"
        )
    }
    new = [
        doc for doc in found
        if doc.get("url") and doc["url"] not in known_urls and doc["url"] not in already_flagged
    ]

    for doc in new:
        execute(
            "INSERT INTO review_queue (kind, ref, payload, note) "
            "VALUES ('roll_section', %s, %s, %s)",
            (doc["url"],
             json.dumps({"filename": doc.get("filename"), "label": doc.get("label")},
                        ensure_ascii=False),
             "New roll supplement on the CEO portal - download it, then parse with "
             "python -m ingest.parse_roll <file> --supplement --load"),
        )
    if new:
        log.warning("%d new roll supplement(s) found on the CEO portal", len(new))
    return {"remote": len(found), "new": len(new), "urls": [d["url"] for d in new][:20]}
