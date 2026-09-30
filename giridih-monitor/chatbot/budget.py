"""Token and cost accounting (LLD 8.4, 13).

Three controls, in increasing severity:

  1. Per-user daily token budget (150k default, 60k for block-role users).
     Exhausted -> polite refusal, admin notified.
  2. Global monthly cap from LLM_MONTHLY_CAP_USD. At DEGRADE_AT_PCT (80%) the
     router stops sending anything to Sonnet and runs every question on Haiku.
  3. At 100% the chatbot goes read-only. The dashboard is unaffected - losing
     the chat must never take the data views down.

Every request writes an llm_usage row whether it succeeded or not, because an
un-billed failure is still money spent.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from common.config import get_settings
from common.logging_setup import get_logger

log = get_logger(__name__)


@dataclass
class Usage:
    model: str
    purpose: str
    input_tokens: int = 0
    cached_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    is_batch: bool = False
    request_id: str | None = None

    def total_tokens(self) -> int:
        return self.input_tokens + self.cached_tokens + self.cache_write_tokens + self.output_tokens


@dataclass
class BudgetState:
    allowed: bool
    degrade_to_haiku: bool
    reason: str = ""
    user_tokens_today: int = 0
    user_daily_budget: int = 0
    month_spend_usd: float = 0.0
    monthly_cap_usd: float = 0.0

    @property
    def month_pct(self) -> float:
        return 100.0 * self.month_spend_usd / self.monthly_cap_usd if self.monthly_cap_usd else 0.0


def price_for(model: str) -> tuple[float, float]:
    """(input, output) USD per million tokens for a model id."""
    p = get_settings().prices
    name = (model or "").lower()
    if "haiku" in name:
        return p.haiku_in, p.haiku_out
    return p.sonnet_in, p.sonnet_out


def estimate_cost(usage: Usage) -> float:
    """Cost in USD. Cached reads bill at ~10% of the input rate; batch work is
    half price (LLD 13)."""
    prices = get_settings().prices
    rate_in, rate_out = price_for(usage.model)

    cost = (
        usage.input_tokens * rate_in
        + usage.cached_tokens * rate_in * prices.cache_read_multiplier
        + usage.cache_write_tokens * rate_in * prices.cache_write_multiplier
        + usage.output_tokens * rate_out
    ) / 1_000_000

    if usage.is_batch:
        cost *= prices.batch_discount
    return round(cost, 6)


def record(usage: Usage, user_id: int | None = None) -> float:
    """Write the llm_usage row. Returns the cost."""
    from common.db import execute

    cost = estimate_cost(usage)
    try:
        execute(
            "INSERT INTO llm_usage (user_id, model, purpose, input_tokens, cached_tokens, "
            "cache_write_tokens, output_tokens, cost_usd, is_batch, request_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (user_id, usage.model, usage.purpose, usage.input_tokens, usage.cached_tokens,
             usage.cache_write_tokens, usage.output_tokens, cost, usage.is_batch, usage.request_id),
        )
    except Exception as exc:
        # Never fail a user's request because accounting failed - but say so.
        log.error("could not record llm_usage (%s): %s", usage.purpose, exc)
    return cost


def check(user_id: int | None, role: str = "strategist") -> BudgetState:
    from common.db import query_one

    settings = get_settings()
    cap = settings.monthly_cap_usd
    default_budget = (settings.block_role_daily_token_budget if role == "block"
                      else settings.default_daily_token_budget)

    row = query_one(
        "SELECT COALESCE(u.daily_token_budget, %s) AS budget, "
        "COALESCE((SELECT SUM(input_tokens + cached_tokens + output_tokens) FROM llm_usage "
        "          WHERE user_id = %s AND ts::date = %s), 0) AS used_today "
        "FROM app_user u WHERE u.user_id = %s",
        (default_budget, user_id, date.today(), user_id),
    ) or {"budget": default_budget, "used_today": 0}

    month = query_one(
        "SELECT COALESCE(SUM(cost_usd), 0) AS spend FROM llm_usage "
        "WHERE date_trunc('month', ts) = date_trunc('month', now())"
    ) or {"spend": 0}

    used = int(row["used_today"] or 0)
    budget = int(row["budget"] or default_budget)
    spend = float(month["spend"] or 0)

    state = BudgetState(
        allowed=True, degrade_to_haiku=False,
        user_tokens_today=used, user_daily_budget=budget,
        month_spend_usd=round(spend, 4), monthly_cap_usd=cap,
    )

    if cap and spend >= cap:
        state.allowed = False
        state.reason = (
            "The monthly assistant budget is exhausted. The dashboard and all data "
            "views still work; chat will resume next month or when an admin raises the cap."
        )
        return state

    if budget and used >= budget:
        state.allowed = False
        state.reason = (
            f"Your daily question budget is used up ({used:,} of {budget:,} tokens). "
            f"It resets at midnight, or an admin can raise it."
        )
        return state

    if cap and spend >= cap * settings.degrade_at_pct / 100.0:
        state.degrade_to_haiku = True
        state.reason = (
            f"{state.month_pct:.0f}% of the monthly budget is used, so answers are running "
            f"on the faster, cheaper model. Analysis may be less detailed."
        )
    return state
