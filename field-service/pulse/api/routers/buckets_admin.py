"""Buckets screen: custom labels awaiting promotion and keyword lists (LLD §10 Buckets).

Keywords are shared by every AC (field.exp_keyword has no AC column), so changing them is admin
only. Promoting a label creates an AC-specific sub-head; finance scope `all`.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from pulse.api.deps import get_state
from pulse.api.ledger_auth import LedgerCaller, at_least
from pulse.api.routers.ledger import http_error, plain
from pulse.db import transaction
from pulse.ledger import labels
from pulse.ledger.errors import LedgerError

router = APIRouter(prefix="/acs/{ac_number}/field")


class PromoteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label_norm: str = Field(min_length=1, max_length=200)
    parent_bucket_id: int
    name_hi: str = Field(min_length=1, max_length=labels.NAME_MAX)
    name_en: str = Field(min_length=1, max_length=labels.NAME_MAX)


class KeywordBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    keyword: str = Field(min_length=1, max_length=labels.NAME_MAX)


def _run(state, fn):
    try:
        with transaction(state.pool) as conn:
            return plain(fn(conn))
    except LedgerError as e:
        raise http_error(e) from None


def _admin(caller: LedgerCaller) -> None:
    if caller.user.role != "admin":
        raise HTTPException(403, "keywords are shared by every AC: admin only")


@router.get("/labels/pending")
def pending(caller: LedgerCaller = Depends(at_least("all")), state=Depends(get_state)):
    return _run(state, lambda conn: {"min_count": labels.REVIEW_MIN_COUNT,
                                     "items": labels.pending(conn, caller.ac.ac_id)})


@router.post("/labels/promote", status_code=201)
def promote(body: PromoteBody, caller: LedgerCaller = Depends(at_least("all")), state=Depends(get_state)):
    return _run(state, lambda conn: labels.promote(conn, caller.ac.ac_id, body.label_norm,
                                                   parent_bucket_id=body.parent_bucket_id, name_hi=body.name_hi,
                                                   name_en=body.name_en, actor_user_id=caller.user.user_id,
                                                   actor_member_id=caller.viewer.member_id))


@router.get("/keywords")
def keywords(caller: LedgerCaller = Depends(at_least("all")), state=Depends(get_state)):
    return _run(state, lambda conn: {"items": labels.keywords(conn, caller.ac.ac_id)})


@router.post("/buckets/{bucket_id}/keywords", status_code=201)
def add_keyword(bucket_id: int, body: KeywordBody, caller: LedgerCaller = Depends(at_least("all")),
                state=Depends(get_state)):
    _admin(caller)
    return _run(state, lambda conn: labels.add_keyword(conn, caller.ac.ac_id, bucket_id, body.keyword,
                                                       actor_user_id=caller.user.user_id,
                                                       actor_member_id=caller.viewer.member_id))


@router.delete("/buckets/{bucket_id}/keywords/{keyword}", status_code=204)
def remove_keyword(bucket_id: int, keyword: str, caller: LedgerCaller = Depends(at_least("all")),
                   state=Depends(get_state)):
    _admin(caller)
    _run(state, lambda conn: labels.remove_keyword(conn, caller.ac.ac_id, bucket_id, keyword,
                                                   actor_user_id=caller.user.user_id,
                                                   actor_member_id=caller.viewer.member_id))
    return Response(status_code=204)
