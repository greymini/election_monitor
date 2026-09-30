"""The tool loop (LLD 8): at most 3 rounds, 20 seconds of wall clock.

A manual loop rather than the SDK tool runner, because every tool result has to
pass through the guard, be logged, and be turned into a citation - and because
the loop has to stop hard on a wall-clock budget. Both caps exist to bound cost:
an unbounded loop on a bad query is the expensive failure mode here.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from chatbot import tools as tool_mod
from chatbot.budget import BudgetState, check, record
from chatbot.llm import cached_system_blocks, get_client, model_kwargs, text_of, usage_from
from chatbot.router import Route, route
from common.logging_setup import get_logger

log = get_logger(__name__)

MAX_ROUNDS = 3
WALL_CLOCK_SECONDS = 20
MAX_TOKENS_ANSWER = 1500
MAX_TOKENS_LOOKUP = 800
MAX_HISTORY_TURNS = 6


@dataclass
class ToolCall:
    name: str
    arguments: dict
    result: str
    is_error: bool
    seconds: float


@dataclass
class Answer:
    text: str
    intent: str
    lang: str
    model: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    charts: list[dict] = field(default_factory=list)
    cost_usd: float = 0.0
    rounds: int = 0
    truncated: bool = False
    degraded: bool = False
    notice: str = ""


def ask(question: str, history: list[dict] | None = None, user_id: int | None = None,
        role: str = "strategist") -> Answer:
    """Answer one question. History is a list of {"role", "content"} dicts."""
    budget: BudgetState = check(user_id, role)
    if not budget.allowed:
        return Answer(text=budget.reason, intent="blocked", lang="en", model="", notice=budget.reason)

    plan: Route = route(question, user_id=user_id, degrade_to_haiku=budget.degrade_to_haiku)
    if plan.intent == "blocked":
        return Answer(text=plan.refusal or "", intent="blocked", lang=plan.lang, model="")

    client = get_client()
    messages: list[dict] = list((history or [])[-MAX_HISTORY_TURNS * 2:])
    messages.append({"role": "user", "content": question})

    answer = Answer(text="", intent=plan.intent, lang=plan.lang, model=plan.model,
                    degraded=plan.degraded)
    if plan.degraded:
        answer.notice = budget.reason

    max_tokens = MAX_TOKENS_ANSWER if plan.intent in {"analysis", "news"} else MAX_TOKENS_LOOKUP
    started = time.perf_counter()

    for round_no in range(1, MAX_ROUNDS + 1):
        answer.rounds = round_no
        request = {
            "model": plan.model,
            "max_tokens": max_tokens,
            "system": cached_system_blocks(),
            "messages": messages,
            **model_kwargs(plan.model),
        }
        if plan.use_tools:
            request["tools"] = tool_mod.TOOL_DEFS

        try:
            response = client.messages.create(**request)
        except Exception as exc:
            log.error("model call failed: %s", exc)
            answer.text = (
                "The assistant could not complete this request. The dashboard and data views "
                "are unaffected. Details have been logged."
            )
            return answer

        answer.cost_usd += record(
            usage_from(response, plan.model, f"chat.{plan.intent}"), user_id
        )

        if response.stop_reason != "tool_use":
            answer.text = text_of(response).strip()
            return answer

        messages.append({"role": "assistant", "content": response.content})
        tool_results = []
        for block in response.content:
            if getattr(block, "type", "") != "tool_use":
                continue
            t0 = time.perf_counter()
            result, is_error = tool_mod.execute(block.name, dict(block.input or {}))
            elapsed = time.perf_counter() - t0

            answer.tool_calls.append(ToolCall(block.name, dict(block.input or {}),
                                              result, is_error, round(elapsed, 3)))
            log.info("tool %s (%s) in %.2fs", block.name,
                     "error" if is_error else "ok", elapsed)

            if block.name == "make_chart" and not is_error:
                import json as _json

                try:
                    answer.charts.append(_json.loads(result)["chart"])
                except Exception:
                    pass

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result,
                **({"is_error": True} if is_error else {}),
            })

        messages.append({"role": "user", "content": tool_results})

        if time.perf_counter() - started > WALL_CLOCK_SECONDS:
            answer.truncated = True
            log.warning("tool loop hit the %ds wall clock after %d round(s)",
                        WALL_CLOCK_SECONDS, round_no)
            break

    # Out of rounds or out of time: ask for a final answer with no tools.
    answer.truncated = True
    try:
        final = client.messages.create(
            model=plan.model,
            max_tokens=max_tokens,
            system=cached_system_blocks(),
            messages=messages + [{
                "role": "user",
                "content": (
                    "Answer now from what you already retrieved. If it is not enough, say "
                    "exactly what is missing. Do not request more tools."
                ),
            }],
            **model_kwargs(plan.model),
        )
        answer.cost_usd += record(usage_from(final, plan.model, f"chat.{plan.intent}.final"), user_id)
        answer.text = text_of(final).strip()
    except Exception as exc:
        log.error("final model call failed: %s", exc)
        answer.text = "The assistant ran out of time on this question. Try narrowing it."
    return answer
