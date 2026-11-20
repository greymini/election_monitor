"""Match a typed name against a list of members. Never guesses: the caller shows a list unless
exactly one member matches.

A member matches when every typed word is the start of some word of the member's display name or
of their core display name (so Roman typing can find a member whose team name is in Devanagari),
after the same normalisation the classifier uses.
"""
from __future__ import annotations

from typing import Any, Iterable

from pulse.common.text import tokens
from pulse.core_adapter import CoreAdapter


def _names(member: dict[str, Any], core: CoreAdapter) -> list[str]:
    names = [member["display_name"]]
    user = core.get_user(member["user_id"]) if member.get("user_id") else None
    if user is not None:
        names.append(user.display_name)
    return names


def matches(query: str, candidates: Iterable[dict[str, Any]], core: CoreAdapter) -> list[dict[str, Any]]:
    wanted = tokens(query)
    if not wanted:
        return []
    out = []
    for c in candidates:
        for name in _names(c, core):
            words = tokens(name)
            if all(any(w.startswith(q) for w in words) for q in wanted):
                out.append(c)
                break
    return out
