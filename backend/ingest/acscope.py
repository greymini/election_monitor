"""Resolving `--ac` and `--election` to ids, for every loader.

Migration 0014 made the constituency part of nearly every key in the schema:
`election.label` is unique per AC rather than globally, `ps_number` means
nothing without an AC, `booth_uid` is `32-B0147` and comes from a per-AC
sequence, and `ac_id` is NOT NULL on sixteen tables.

The loaders were not all updated with it. `ingest/crosswalk.py` was; before this
module, `ingest/parse_pslist.py`, `ingest/parse_roll.py` and
`analytics/caste_estimate.py` still looked an election up by bare label, built
`booth_uid` as `B0147`, and inserted rows with no `ac_id` at all - which the
NOT NULL constraints reject, so none of them could write a single row against
the current schema. Nothing noticed because until the embedded server in
`scripts/dev_stack.py` existed, no test or command in this repo had reached a
database.

Every loader resolves its scope through this module so there is one answer to
"which constituency is this document about", and so adding a constituency never
means auditing five separate label lookups again.
"""

from __future__ import annotations

from dataclasses import dataclass

from common.logging_setup import get_logger

log = get_logger(__name__)


class AmbiguousScope(ValueError):
    """A label that names a contest in more than one constituency."""


@dataclass(frozen=True)
class ElectionScope:
    """One constituency's contest at one event."""

    election_id: int
    ac_id: int
    ac_number: int
    ac_name: str
    label: str

    def __str__(self) -> str:
        return f"{self.label} in AC {self.ac_number}-{self.ac_name} (election_id {self.election_id})"


def resolve_election(cur, label: str, ac_number: int | None = None) -> ElectionScope:
    """The contest labelled `label`, in `ac_number` when given.

    Refuses rather than guesses when the label is ambiguous. Guessing here would
    load one constituency's booth results against another's election_id, and
    every downstream view would still be internally consistent - the kind of
    error that is invisible until somebody checks a number by hand.
    """
    sql = ("SELECT e.election_id, e.ac_id, a.ac_number, a.name_en, e.label "
           "FROM election e JOIN ac a ON a.ac_id = e.ac_id WHERE e.label = %s")
    params: list[object] = [label]
    if ac_number is not None:
        sql += " AND a.ac_number = %s"
        params.append(ac_number)
    cur.execute(sql + " ORDER BY a.ac_number", params)
    rows = cur.fetchall()

    if not rows:
        where = f" in AC {ac_number}" if ac_number is not None else ""
        raise ValueError(
            f"no election labelled {label!r}{where}. Check that the AC is seeded "
            f"(db/seed/ac.csv) and that db/seed/election_event.csv lists the event."
        )
    if len(rows) > 1:
        names = ", ".join(f"{r['ac_number']}-{r['name_en']}" for r in rows)
        raise AmbiguousScope(
            f"election label {label!r} exists in {len(rows)} constituencies ({names}). "
            f"Pass --ac to say which one this document belongs to."
        )
    row = rows[0]
    return ElectionScope(
        election_id=row["election_id"], ac_id=row["ac_id"], ac_number=row["ac_number"],
        ac_name=row["name_en"], label=row["label"],
    )


def resolve_ac(cur, ac_number: int) -> tuple[int, str]:
    """(ac_id, name_en) for an AC number."""
    cur.execute("SELECT ac_id, name_en FROM ac WHERE ac_number = %s", (ac_number,))
    row = cur.fetchone()
    if row is None:
        raise ValueError(f"AC {ac_number} is not seeded. Add it to db/seed/ac.csv.")
    return row["ac_id"], row["name_en"]


def default_block(cur, ac_id: int) -> int | None:
    """The lowest-numbered block in this AC, as a fallback for an unplaced area.

    Only a fallback: `resolve_area` prefers an alias match, and a PS whose area
    cannot be placed goes to the review queue rather than into an arbitrary
    block. Returning the lowest block id rather than any block keeps a re-run
    deterministic.
    """
    cur.execute("SELECT block_id FROM block WHERE ac_id = %s ORDER BY block_id LIMIT 1",
                (ac_id,))
    row = cur.fetchone()
    return row["block_id"] if row else None


def add_ac_argument(parser, required: bool = False) -> None:
    """`--ac 32`, worded the same way on every loader."""
    parser.add_argument(
        "--ac", type=int, required=required, metavar="N",
        help="AC number, e.g. 32 for Giridih. Required when more than one "
             "constituency has a contest with this election label.",
    )
