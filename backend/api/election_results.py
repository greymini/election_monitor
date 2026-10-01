"""Constituency-level results per candidate, and the provenance behind them.

`/summary`, `/elections/{label}/candidates` and the chatbot all describe the
same declared result, so the arithmetic lives here once. The figures follow the
Form 20 itself:

* booth rows (`result_booth`) are the EVM count;
* postal ballots exist only at constituency level (`result_ac_total` metric
  `postal`, per candidate and for NOTA);
* the declared total is EVM + postal, which is what decides the winner.

An election with no booth rows still has the published totals seeded in
`result_ac_total` (metric `votes`); those are returned with `basis =
'published'` so a page can show them, labelled, instead of a hardcoded list.
"""

from __future__ import annotations

from common.db import query, query_one

UNRECORDED_PARTY = "UNK"

# Section 158 of the Representation of the People Act 1951: a candidate who is
# not elected and polls not more than one sixth of the valid votes polled by all
# candidates forfeits the deposit. NOTA is not a candidate, so its votes are not
# in that total (ECI clarification, 2013).
DEPOSIT_RULE = (
    "Deposit forfeited: not elected and not more than one sixth of the valid votes "
    "polled by all candidates (RP Act 1951, s.158). NOTA votes are excluded from "
    "that total."
)


def candidate_results(ac_id: int, election_label: str) -> dict | None:
    """Every candidate in one election, ranked, with EVM, postal and total votes.

    Returns None when the election does not exist in this AC.
    """
    election = query_one(
        "SELECT e.election_id, e.label, e.type, e.year, "
        "EXISTS (SELECT 1 FROM result_booth r WHERE r.election_id = e.election_id) "
        "  AS has_booths "
        "FROM election e WHERE e.ac_id = %s AND e.label = %s",
        (ac_id, election_label),
    )
    if election is None:
        return None

    rows = query(
        "WITH evm AS ("
        "  SELECT candidate_id, SUM(votes)::INT AS votes FROM result_booth"
        "  WHERE election_id = %(e)s GROUP BY candidate_id"
        "), led AS ("
        # mv_result_booth_wide breaks a booth tie by contestant key, as the
        # ranking everywhere else does, so the counts here add up to its rows.
        "  SELECT winner_candidate, COUNT(*)::INT AS booths"
        "  FROM mv_result_booth_wide WHERE election_id = %(e)s GROUP BY winner_candidate"
        ")"
        "SELECT c.candidate_id, c.name_en AS candidate, c.name_hi AS candidate_hi,"
        "       p.abbr AS party, p.name_en AS party_name, p.name_hi AS party_name_hi,"
        "       c.is_winner, evm.votes AS evm_votes, pt.value AS postal_votes,"
        "       pub.value AS published_votes, pub.source,"
        "       COALESCE(led.booths, 0) AS booths_led "
        "FROM candidate c "
        "LEFT JOIN party p ON p.party_id = c.party_id "
        "LEFT JOIN evm ON evm.candidate_id = c.candidate_id "
        "LEFT JOIN result_ac_total pt ON pt.candidate_id = c.candidate_id AND pt.metric = 'postal' "
        "LEFT JOIN result_ac_total pub ON pub.candidate_id = c.candidate_id AND pub.metric = 'votes' "
        "LEFT JOIN led ON led.winner_candidate = c.name_en "
        "WHERE c.election_id = %(e)s",
        {"e": election["election_id"]},
    )

    has_booths = election["has_booths"]
    nota = None
    candidates: list[dict] = []
    for r in rows:
        if has_booths:
            total = (r["evm_votes"] or 0) + (r["postal_votes"] or 0)
        else:
            total = r["published_votes"]
            r["evm_votes"] = None
        r["votes"] = total
        r["party_recorded"] = r["party"] not in (None, UNRECORDED_PARTY)
        if r["party"] == "NOTA":
            nota = {"votes": total, "evm_votes": r["evm_votes"],
                    "postal_votes": r["postal_votes"], "source": r["source"]}
            continue
        if total is None:
            continue
        candidates.append(r)

    candidates.sort(key=lambda r: (-r["votes"], r["candidate"]))
    polled_by_candidates = sum(r["votes"] for r in candidates)
    valid = polled_by_candidates + (nota["votes"] if nota else 0)
    winner_votes = candidates[0]["votes"] if candidates else None
    for rank, r in enumerate(candidates, 1):
        r["rank"] = rank
        r["share_pct"] = round(100 * r["votes"] / valid, 2) if valid else None
        elected = r["is_winner"] or (rank == 1 and len(candidates) > 1)
        r["is_winner"] = elected
        r["deposit_forfeited"] = (
            None if not polled_by_candidates
            else (not elected and 6 * r["votes"] <= polled_by_candidates)
        )
        r["behind_winner"] = None if winner_votes is None else winner_votes - r["votes"]
        if not has_booths:
            r["booths_led"] = None
        del r["published_votes"]

    if nota is not None:
        nota["share_pct"] = round(100 * nota["votes"] / valid, 2) if valid else None

    return {
        "election": {"label": election["label"], "type": election["type"],
                     "year": election["year"]},
        "basis": "form20" if has_booths else "published",
        "valid_votes": valid,
        "votes_polled_by_candidates": polled_by_candidates,
        "candidates": candidates,
        "nota": nota,
        "sources": election_sources(ac_id).get(election["label"], []),
        "deposit_rule": DEPOSIT_RULE,
        "notes": _notes(has_booths, candidates),
    }


def _notes(has_booths: bool, candidates: list[dict]) -> list[str]:
    notes = []
    if has_booths:
        notes.append("Votes are the Form 20 'Total Votes Polled' row: EVM votes counted at "
                     "polling stations plus postal ballots, which Form 20 reports only for "
                     "the whole constituency. Booths led uses EVM votes.")
    else:
        notes.append("No Form 20 is loaded for this election. Votes are the published "
                     "constituency totals, from the source named on each row.")
    unrecorded = sum(1 for c in candidates if not c["party_recorded"])
    if unrecorded:
        notes.append(f"{unrecorded} candidate(s) have no party in the loaded sources. "
                     "Form 20 prints names only; affiliations come from a separate list "
                     "(db/seed/form20/candidate_parties.csv).")
    return notes


def election_sources(ac_id: int) -> dict[str, list[dict]]:
    """Per election label: the documents its booth rows were read from."""
    rows = query(
        "SELECT e.label, m.source_doc, COUNT(*)::INT AS booth_rows,"
        "       MIN(m.source_page) AS first_page, MAX(m.source_page) AS last_page,"
        "       s.sha256, s.kind, s.storage_key, s.parse_status,"
        "       COALESCE(s.is_synthetic, false) AS synthetic "
        "FROM result_booth_meta m "
        "JOIN election e ON e.election_id = m.election_id "
        "LEFT JOIN LATERAL ("
        "  SELECT * FROM source_doc d"
        "  WHERE d.filename = m.source_doc OR d.filename LIKE '%%/' || m.source_doc"
        "  ORDER BY d.doc_id DESC LIMIT 1"
        ") s ON true "
        "WHERE m.ac_id = %s "
        "GROUP BY e.label, m.source_doc, s.sha256, s.kind, s.storage_key, s.parse_status,"
        "         s.is_synthetic "
        "ORDER BY e.label, m.source_doc",
        (ac_id,),
    )
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r.pop("label"), []).append(r)
    return out


def booths_led(ac_id: int) -> dict[str, list[dict]]:
    """Per election label: how many polling stations each candidate led (EVM votes)."""
    rows = query(
        "SELECT election_label, winner_party AS party, winner_candidate AS candidate,"
        "       COUNT(*)::INT AS booths "
        "FROM mv_result_booth_wide WHERE ac_id = %s AND winner_candidate IS NOT NULL "
        "GROUP BY election_label, winner_party, winner_candidate "
        "ORDER BY election_label, booths DESC, candidate",
        (ac_id,),
    )
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r.pop("election_label"), []).append(r)
    return out


def published_results(ac_id: int) -> dict[str, dict]:
    """Per election label: winner, runner-up and margin from the published totals.

    For elections without a Form 20 loaded; the frontend used to carry these as
    a hardcoded list.
    """
    rows = query(
        "WITH ranked AS ("
        "  SELECT e.label, c.name_en AS candidate, p.abbr AS party, t.value AS votes,"
        "         t.source,"
        "         ROW_NUMBER() OVER (PARTITION BY e.election_id"
        "                            ORDER BY t.value DESC, c.name_en) AS rn"
        "  FROM result_ac_total t"
        "  JOIN candidate c ON c.candidate_id = t.candidate_id"
        "  JOIN election e ON e.election_id = t.election_id"
        "  LEFT JOIN party p ON p.party_id = c.party_id"
        "  WHERE e.ac_id = %s AND t.metric = 'votes' AND p.abbr IS DISTINCT FROM 'NOTA'"
        ") "
        "SELECT w.label, w.candidate AS winner_candidate, w.party AS winner_party,"
        "       w.votes AS winner_votes, r.candidate AS runner_candidate,"
        "       r.party AS runner_party, r.votes AS runner_votes,"
        "       w.votes - r.votes AS margin_votes, w.source "
        "FROM ranked w JOIN ranked r ON r.label = w.label AND r.rn = 2 "
        "WHERE w.rn = 1",
        (ac_id,),
    )
    return {r.pop("label"): r for r in rows}


def any_synthetic(ac_id: int) -> bool:
    """Whether any loaded document for this AC is generated test data."""
    row = query_one(
        "SELECT EXISTS (SELECT 1 FROM source_doc WHERE is_synthetic "
        "AND (ac_id = %s OR ac_id IS NULL)) AS synthetic",
        (ac_id,),
    )
    return bool(row and row["synthetic"])
