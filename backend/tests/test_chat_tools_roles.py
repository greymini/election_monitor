"""The chatbot's tools honour the asking user's role (audit E2).

Chat is parked (CHAT_ENABLED=false), but if it is switched on, a block-level
user must not be able to read what the dashboard hides from them: the booth
card tool built every card with include_caste=True, and run_sql reads
caste_estimate and every block.
"""

from __future__ import annotations

import pytest

from chatbot import tools


@pytest.fixture
def card_calls(monkeypatch):
    calls = []
    import api.booth_card

    def fake(booth_uid, include_caste=True, ac_id=None):
        calls.append(include_caste)
        return {"booth": {"booth_uid": booth_uid}}

    monkeypatch.setattr(api.booth_card, "build_booth_card", fake)
    return calls


@pytest.mark.parametrize("role, sees_caste", [("admin", True), ("strategist", True),
                                               ("block", False)])
def test_the_booth_card_tool_hides_caste_from_block_users(card_calls, role, sees_caste):
    text, is_error = tools.execute("get_booth_card", {"booth_uid": "32-B0001"}, role=role)
    assert not is_error
    assert card_calls == [sees_caste]


def test_a_block_user_cannot_run_raw_sql(monkeypatch):
    monkeypatch.setattr(tools, "tool_run_sql", lambda **_: pytest.fail("must not run"))
    text, is_error = tools.execute("run_sql", {"sql": "SELECT 1"}, role="block")
    assert is_error and "not available" in text


def test_the_agent_passes_the_role_through():
    import inspect

    from chatbot import agent

    assert "role=role" in inspect.getsource(agent.ask)
