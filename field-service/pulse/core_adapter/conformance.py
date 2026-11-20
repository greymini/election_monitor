"""Conformance checks any `CoreAdapter` must pass: the contract this service relies on.

Run against the stub (always) and against any other adapter with:

    PULSE_CONFORMANCE_TARGET=package.module:factory python -m pytest tests/test_adapter_conformance.py

`factory()` takes no arguments and returns a `Target`: the adapter, one user id that exists, and
(optionally) a way to get a valid bearer token for that user. A real adapter usually reads a token from
the environment; the checks that need one are skipped when it is missing.

The checks are written for read-only use: none of them changes the core.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pulse.core_adapter.base import Ac, Area, Block, CoreAdapter, CoreUser

ROLES = frozenset({"admin", "strategist", "block", "rep"})
AREA_KINDS = frozenset({"panchayat", "ward"})
UNKNOWN = "conformance-no-such-id-7f3a"


@dataclass
class Target:
    adapter: CoreAdapter
    user_id: str                                  # a user that exists in the core
    token_for: Callable[[str], str] | None = None  # valid bearer token for a user, if the target can make one


class Skip(Exception):
    """A check that cannot run for this target (for example: no token available)."""


def _acs(t: Target) -> list[Ac]:
    acs = t.adapter.list_acs()
    assert acs, "list_acs() returned nothing"
    return acs


def check_protocol(t: Target) -> None:
    assert isinstance(t.adapter, CoreAdapter), "adapter does not implement the CoreAdapter protocol"


def check_acs(t: Target) -> None:
    acs = _acs(t)
    assert all(isinstance(a, Ac) for a in acs)
    assert len({a.ac_id for a in acs}) == len(acs), "ac_id not unique"
    assert len({a.ac_number for a in acs}) == len(acs), "ac_number not unique"
    for a in acs:
        assert isinstance(a.ac_id, str) and a.ac_id, a
        assert isinstance(a.ac_number, int) and a.ac_number > 0, a
        assert a.name_en.strip() and a.name_hi.strip(), f"AC {a.ac_id} needs both names"


def check_blocks(t: Target) -> None:
    for ac in _acs(t):
        blocks = t.adapter.list_blocks(ac.ac_id)
        assert all(isinstance(b, Block) for b in blocks)
        assert len({b.block_id for b in blocks}) == len(blocks), f"block_id not unique in {ac.ac_id}"
        for b in blocks:
            assert b.ac_id == ac.ac_id, f"list_blocks({ac.ac_id}) returned {b}"
            assert isinstance(b.block_id, str) and b.block_id
            assert b.name_en.strip() and b.name_hi.strip(), f"block {b.block_id} needs both names"


def check_areas(t: Target) -> None:
    for ac in _acs(t):
        block_ids = {b.block_id for b in t.adapter.list_blocks(ac.ac_id)}
        areas = t.adapter.list_areas(ac.ac_id)
        assert all(isinstance(a, Area) for a in areas)
        assert len({a.area_id for a in areas}) == len(areas), f"area_id not unique in {ac.ac_id}"
        for a in areas:
            assert a.ac_id == ac.ac_id, f"list_areas({ac.ac_id}) returned {a}"
            assert a.block_id in block_ids, f"area {a.area_id} names block {a.block_id}, not in list_blocks"
            assert a.kind in AREA_KINDS, f"area {a.area_id} kind {a.kind!r}"
            assert a.name_en.strip() and a.name_hi.strip(), f"area {a.area_id} needs both names"


def check_areas_by_block(t: Target) -> None:
    for ac in _acs(t):
        everything = {a.area_id for a in t.adapter.list_areas(ac.ac_id)}
        seen: set[str] = set()
        for b in t.adapter.list_blocks(ac.ac_id):
            part = t.adapter.list_areas(ac.ac_id, b.block_id)
            assert all(a.block_id == b.block_id for a in part), f"list_areas({ac.ac_id}, {b.block_id}) leaks other blocks"
            seen |= {a.area_id for a in part}
        assert seen == everything, "areas filtered by block do not add up to all areas"


def check_unknown_ids_are_empty_not_errors(t: Target) -> None:
    assert t.adapter.list_blocks(UNKNOWN) == []
    assert t.adapter.list_areas(UNKNOWN) == []
    assert t.adapter.get_user(UNKNOWN) is None
    assert t.adapter.electors_for_area(UNKNOWN) is None


def check_electors(t: Target) -> None:
    """An int (0 or more) from a linked roll snapshot, or None when there is none: never a default."""
    for ac in _acs(t):
        for a in t.adapter.list_areas(ac.ac_id):
            n = t.adapter.electors_for_area(a.area_id)
            assert n is None or (isinstance(n, int) and not isinstance(n, bool) and n >= 0), f"{a.area_id}: {n!r}"


def check_user(t: Target) -> None:
    u = t.adapter.get_user(t.user_id)
    assert isinstance(u, CoreUser), f"get_user({t.user_id!r}) returned {u!r}"
    assert u.user_id == t.user_id
    assert u.display_name.strip()
    assert u.role in ROLES, f"role {u.role!r} not one of {sorted(ROLES)}"
    assert isinstance(u.ac_ids, tuple) and isinstance(u.block_ids, tuple)
    known = {a.ac_id for a in _acs(t)}
    assert set(u.ac_ids) <= known, f"user names ACs not in list_acs: {set(u.ac_ids) - known}"
    blocks = {b.block_id for ac in u.ac_ids for b in t.adapter.list_blocks(ac)}
    assert set(u.block_ids) <= blocks, f"user names blocks outside their ACs: {set(u.block_ids) - blocks}"


def check_bad_tokens_refused(t: Target) -> None:
    for token in ("", "x", "Bearer", "a.b.c", "eyJhbGciOiJub25lIn0.eyJzdWIiOiJ4In0."):
        assert t.adapter.verify_token(token) is None, f"verify_token accepted {token!r}"


def check_valid_token(t: Target) -> None:
    if t.token_for is None:
        raise Skip("no token for this target (set it in the factory)")
    token = t.token_for(t.user_id)
    u = t.adapter.verify_token(token)
    assert u is not None and u.user_id == t.user_id, "a valid token was refused"
    assert u == t.adapter.get_user(t.user_id), "verify_token and get_user disagree about the user"
    assert t.adapter.verify_token(token[:-2] + ("AA" if not token.endswith("AA") else "BB")) is None, "a tampered token was accepted"


def check_stable(t: Target) -> None:
    """Two calls give the same answer: the service caches nothing and expects none to be needed."""
    assert t.adapter.list_acs() == t.adapter.list_acs()
    for ac in _acs(t):
        assert t.adapter.list_areas(ac.ac_id) == t.adapter.list_areas(ac.ac_id)


CHECKS: list[Callable[[Target], None]] = [
    check_protocol, check_acs, check_blocks, check_areas, check_areas_by_block,
    check_unknown_ids_are_empty_not_errors, check_electors, check_user, check_bad_tokens_refused,
    check_valid_token, check_stable,
]


def stub_target() -> Target:
    """The stub with its seed data: what the rest of the suite runs against."""
    from pulse.core_adapter.stub import StubCoreAdapter

    stub = StubCoreAdapter(jwt_secret="conformance-secret-0123456789abcdef0123")
    return Target(adapter=stub, user_id="usr-principal", token_for=stub.issue_token)
