"""The one boundary to the core platform.

Everything this service needs from the existing platform (users and roles, token verification,
constituencies, blocks, areas, elector counts) goes through `CoreAdapter`. Ids are opaque strings:
no other module may assume how the core stores them.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class CoreUser:
    user_id: str
    display_name: str
    role: str                      # admin | strategist | block | rep
    ac_ids: tuple[str, ...]        # ACs the user may act in
    block_ids: tuple[str, ...] = ()  # (AC, block) scope for block users; empty = whole AC


@dataclass(frozen=True)
class Ac:
    ac_id: str
    ac_number: int
    name_en: str
    name_hi: str


@dataclass(frozen=True)
class Block:
    block_id: str
    ac_id: str
    name_en: str
    name_hi: str


@dataclass(frozen=True)
class Area:
    area_id: str
    ac_id: str
    block_id: str
    kind: str                      # panchayat | ward
    name_en: str
    name_hi: str


@runtime_checkable
class CoreAdapter(Protocol):
    def get_user(self, user_id: str) -> CoreUser | None: ...

    def verify_token(self, token: str) -> CoreUser | None:
        """Return the user for a valid bearer token, else None."""
        ...

    def list_acs(self) -> list[Ac]: ...

    def list_blocks(self, ac_id: str) -> list[Block]: ...

    def list_areas(self, ac_id: str, block_id: str | None = None) -> list[Area]: ...

    def electors_for_area(self, area_id: str) -> int | None:
        """Electors from the linked roll snapshot; None when no snapshot is linked."""
        ...


def ac_by_number(core: CoreAdapter, ac_number: int) -> Ac | None:
    return next((a for a in core.list_acs() if a.ac_number == ac_number), None)
