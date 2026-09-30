"""Anthropic client, cached prompt assembly and usage accounting.

Two deviations from LLD 8.3, both forced by the current API:

  * `temperature` is REJECTED with a 400 on Sonnet 5. The LLD asks for
    temperature 0 on both models; on Sonnet 5 the equivalent lever is
    `output_config.effort`, which is what ANALYSIS_EFFORT sets. Haiku 4.5 still
    accepts temperature, so the router and lookup calls keep temperature 0.
  * The canonical Haiku 4.5 model id carries no date suffix.

Prompt cache layout (LLD 8.1), in order, all inside ONE system block list:
  1. role, rules, refusal policy, citation format   (system_cached.md)
  2. schema documentation                            (schema_doc.md)
  3. knowledge-card digest                           (knowledge cards)
  4. <- cache_control breakpoint here
Conversation turns and tool results follow after, uncached.

The cached prefix must be byte-identical between requests or the cache misses
entirely, so nothing volatile - no timestamp, no user id, no request id - may
appear in it.
"""

from __future__ import annotations

import functools
from pathlib import Path

from chatbot.budget import Usage
from common.config import get_settings
from common.logging_setup import get_logger

log = get_logger(__name__)

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"


@functools.lru_cache(maxsize=1)
def get_client():
    import anthropic

    settings = get_settings()
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    return anthropic.Anthropic(api_key=settings.anthropic_api_key, timeout=60.0, max_retries=2)


def _read(name: str) -> str:
    path = PROMPT_DIR / name
    return path.read_text(encoding="utf-8") if path.exists() else ""


@functools.lru_cache(maxsize=1)
def knowledge_digest() -> str:
    """Knowledge cards flagged in_prompt, newest review first.

    Falls back to the seed files when the database is unreachable so the
    assistant still has its guardrails in a degraded environment.
    """
    cards: list[str] = []
    try:
        from common.db import query

        rows = query(
            "SELECT slug, topic, title_en, body_en FROM knowledge_card "
            "WHERE in_prompt ORDER BY slug"
        )
        cards = [f"### {r['title_en'] or r['slug']} ({r['topic']})\n\n{r['body_en']}" for r in rows]
    except Exception as exc:
        log.warning("knowledge cards unavailable from the database (%s) - using seed files", exc)
        seed = Path(__file__).resolve().parents[1] / "db" / "seed" / "knowledge_cards"
        if seed.exists():
            for path in sorted(seed.glob("*.md")):
                text = path.read_text(encoding="utf-8")
                body = text.split("---", 2)[-1].strip() if text.startswith("---") else text
                cards.append(f"### {path.stem}\n\n{body}")
    return "\n\n".join(cards)


@functools.lru_cache(maxsize=1)
def cached_system_blocks() -> list[dict]:
    """The cacheable system prefix. One text block, one breakpoint at its end."""
    parts = [
        _read("system_cached.md"),
        "# Database schema\n\n" + _read("schema_doc.md"),
        "# Knowledge cards\n\n" + knowledge_digest(),
    ]
    return [{
        "type": "text",
        "text": "\n\n---\n\n".join(p for p in parts if p.strip()),
        "cache_control": {"type": "ephemeral"},
    }]


def usage_from(response, model: str, purpose: str, is_batch: bool = False) -> Usage:
    """Pull token counts off a Message, including both cache fields."""
    u = getattr(response, "usage", None)
    return Usage(
        model=model,
        purpose=purpose,
        input_tokens=getattr(u, "input_tokens", 0) or 0,
        cached_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
        cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
        output_tokens=getattr(u, "output_tokens", 0) or 0,
        is_batch=is_batch,
        request_id=getattr(response, "id", None),
    )


def model_kwargs(model: str) -> dict:
    """Per-model parameters. Sonnet 5 takes effort; Haiku 4.5 takes temperature.

    Sending `temperature` to Sonnet 5 returns a 400, so the two are mutually
    exclusive rather than merely different.
    """
    settings = get_settings()
    if "haiku" in model.lower():
        return {"temperature": 0}
    return {"output_config": {"effort": settings.analysis_effort}}


def text_of(response) -> str:
    """Concatenate the text blocks of a Message."""
    return "".join(b.text for b in getattr(response, "content", []) if getattr(b, "type", "") == "text")
