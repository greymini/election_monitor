"""The job registry. One place that maps a name to a callable, used by both the
scheduler and the manual runner, so `python -m worker.run <name>` can run
exactly what the scheduler runs."""

from __future__ import annotations

from collections.abc import Callable

from common.jobs import job_context
from common.logging_setup import get_logger

log = get_logger(__name__)


def _news_crawl() -> dict:
    from news.crawl_rss import crawl

    return crawl()


def _news_label_batch() -> dict:
    from news.label_batch import submit

    return submit()


def _news_label_collect() -> dict:
    from news.label_collect import collect, open_batches

    totals = {"batches": 0, "labelled": 0}
    for batch_id in open_batches():
        stats = collect(batch_id)
        totals["batches"] += 1
        totals["labelled"] += stats.get("labelled", 0)
    return totals


def _news_embed() -> dict:
    from news.embed import embed_ground_reports, embed_news

    return {"news": embed_news(), "ground_reports": embed_ground_reports()}


def _analytics_refresh() -> dict:
    from analytics.refresh import refresh_all

    timings = refresh_all()
    return {"views": len(timings), "seconds": round(sum(timings.values()), 2)}


def _analytics_caste() -> dict:
    from analytics.caste_estimate import refresh

    return refresh()


def _ceo_check_supplement() -> dict:
    from worker.ops import check_new_supplement

    return check_new_supplement()


def _ops_backup() -> dict:
    from worker.ops import backup

    return backup()


def _ops_usage_report() -> dict:
    from worker.ops import usage_report

    return usage_report()


def _ops_purge_prompts() -> dict:
    from worker.ops import purge_prompts

    return purge_prompts()


REGISTRY: dict[str, Callable[[], dict]] = {
    "news.crawl": _news_crawl,
    "news.label_batch": _news_label_batch,
    "news.label_collect": _news_label_collect,
    "news.embed": _news_embed,
    "analytics.refresh": _analytics_refresh,
    "analytics.caste_estimate": _analytics_caste,
    "ceo.check_new_supplement": _ceo_check_supplement,
    "ops.backup": _ops_backup,
    "ops.usage_report": _ops_usage_report,
    "ops.purge_prompts": _ops_purge_prompts,
}


def run_job(name: str) -> dict:
    """Run one registered job inside a job_run row. Never raises to the caller -
    the scheduler must survive a failing job."""
    fn = REGISTRY.get(name)
    if fn is None:
        raise KeyError(f"unknown job {name!r}; known: {', '.join(sorted(REGISTRY))}")
    try:
        with job_context(name) as job:
            result = fn() or {}
            job.set(**{k: v for k, v in result.items() if isinstance(v, (int, float, str, bool))})
            job.log_line(str(result)[:2000])
            return result
    except Exception as exc:
        log.exception("job %s failed", name)
        return {"error": str(exc)}
