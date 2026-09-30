"""Form 20 column -> candidate and party (master prompt 3.1).

Audit finding C1, which the audit calls the single most consequential defect in
the system, and the first of five steps in its "shortest path to a trustworthy
booth-level margin".

What was wrong. `resolve_candidates` split a header cell on a trailing
parenthesis to find a party abbreviation, then looked that up against `abbr` and
`name_en` - never `name_hi`, which is seeded for all thirteen parties. Two
consequences, both silent:

  * A real Form 20 header carries the candidate's **name**, not `Name (ABBR)`,
    so there was usually nothing in brackets at all and `party_id` came out
    NULL.
  * Where a header *did* carry a party, it carried it in Devanagari
    ("सुदिव्य कुमार (झामुमो)"), which could not match a lookup built from
    Latin abbreviations by construction.

So every candidate loaded unattributed, `mv_result_booth_party` grouped them all
into one `party_id = -1` bucket, and `mv_result_booth_wide` reported
`margin_pct = 100.00` with a NULL winner for every booth in the constituency.
The Results page, the map, the area rollup, the transfer view, the priority
score and the scenario baseline were all built on that.

The resolution order here is master prompt 3.1, and each step exists because
some real document needs it:

  1. **Normalise** the header cell - NFC, strip, collapse whitespace,
     Devanagari nukta and chandrabindu normalisation, punctuation removed except
     parentheses.
  2. **Bracket text against `party_alias`**, script-aware. This is what makes
     "(झामुमो)" resolve; the alias table carries 80 spellings across both
     scripts, including two OCR variants.
  3. **Fuzzy-match the name** against that election's seeded candidates from
     `result_ac_total`, Jaro-Winkler on transliterated Latin, threshold 0.88.
     This is the usual path, because the usual header is a bare name.
  4. **NOTA** in either script resolves to the NOTA party, and loads as a
     candidate row - it has to, or it cannot enter the denominator (D1).
  5. **Independents** resolve to `party=IND` with a distinct `candidate_id`, so
     they rank individually rather than as a bucket (D3).
  6. **Any unresolved column aborts the load**, with a review-queue item
     carrying the raw header and the top three guesses with their scores.

That last rule is the important one. The previous behaviour was to log a warning
and load the column unattributed, which is how a 100% margin reached the
dashboard. A Form 20 whose columns cannot all be resolved is a Form 20 we do not
understand, and loading 90% of it is worse than loading none: the AC totals
still reconcile if the unresolved column is small, so nothing downstream
notices.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from common.logging_setup import get_logger
from common.similarity import jaro_winkler
from common.textnorm import alias_key, normalize_text, to_latin

log = get_logger(__name__)

# Master prompt 3.1 step 3. Higher than the crosswalk's 0.85 because a
# mismatched candidate is worse than a mismatched booth: it attributes votes to
# the wrong person, and the AC total check will not catch it if two candidates
# are swapped.
NAME_MATCH_THRESHOLD = 0.88

# The best match must also beat the second-best by this much. This guards
# against ambiguity: two candidates with similar names is exactly when a wrong
# attribution is most plausible and least visible, and picking the higher of two
# near-equal scores is a coin flip dressed up as a decision.
NAME_MATCH_MARGIN = 0.05

# ...and the first name-part must itself clear the threshold.
#
# This one is an addition beyond what was specified, because the two rules above
# do not achieve what they were asked to achieve, and the measurement says so:
#
#   'Sudhir Kumat' against the seeded 'Sudivya Kumar'
#       whole-string        0.8833   (>= 0.88, so rule 1 passes)
#       margin over 2nd     0.3368   (>= 0.05, so rule 2 passes)
#       first name-part     0.8444   (< 0.88, so this rule rejects it)
#
# The margin rule catches ambiguity between two similar candidates; it cannot
# catch a single spurious near-match, which is a different failure. A whole-string
# metric can be carried by one strong part while another part is wrong - here
# 'kumat'/'kumar' scores 0.92 and drags 'sudhir'/'sudivy' over the line.
#
# The *first* part rather than every part, because middle name-parts are exactly
# what gets abbreviated on a Form 20: 'Nirbhay Kr Shahabadi' for 'Nirbhay Kumar
# Shahabadi' scores 0.5667 on 'kr'/'kumar' and must still resolve. The given name
# is the least abbreviated and most distinctive part. Measured over every header
# spelling in the fixtures, this separates the legitimate variants (all 1.0 on
# the first part) from the spurious match (0.8444) without a single exception.
FIRST_PART_THRESHOLD = 0.88

NOTA_TOKENS = {
    "nota",
    "नोटा",
    "none of the above",
    "इनमें से कोई नहीं",
    "उपरोक्त में से कोई नहीं",
}

# Header cells that are tail totals, not candidates. Kept here rather than in
# the parser so the two cannot disagree about what a candidate column is.
TAIL_TOKENS = {
    "total", "कुल", "total of valid votes", "कुल वैध मत", "valid votes",
    "rejected", "अस्वीकृत मत", "no. of rejected votes", "rejected votes",
    "tendered", "निविदत्त मत", "no. of tendered votes", "tendered votes",
    "serial no. of polling station", "क्रम सं", "polling station",
}


# Devanagari transliteration reinstates inherent vowels and lengthens others,
# so the same name spelled in each script produces different Latin: नवीन आनंद
# becomes 'naveena aananda' while the ECI's own roman spelling is 'navin anand'.
# Scored as-is that pair comes to 0.854, below the 0.88 the master prompt
# specifies - so the honest fix is to compare the two transliterations on an
# equal footing rather than to weaken the threshold, which would let genuinely
# different names through.
_VOWEL_RUNS = re.compile(r"([aeiou])\1+")


def comparable(name: str) -> str:
    """A transliteration-neutral form of a name, for scoring only.

    Collapses doubled vowels ('aa' -> 'a', 'ee' -> 'i' via the run collapse plus
    the map below) and drops a trailing inherent 'a'. Never used for display or
    storage - only to put two spellings of one name on the same footing.
    """
    latin = to_latin(normalize_text(name)).lower()
    latin = latin.replace("ee", "i").replace("oo", "u")
    latin = _VOWEL_RUNS.sub(r"\1", latin)
    words = []
    for word in latin.split():
        # 'ananda' -> 'anand'. Only for words long enough that the trailing
        # vowel is plausibly the inherent one rather than part of a short name.
        if len(word) > 3 and word.endswith("a"):
            word = word[:-1]
        words.append(word)
    return " ".join(words)


@dataclass
class Candidate:
    """A seeded candidate for this election, from `result_ac_total`."""

    candidate_id: int | None
    name_en: str
    party_abbr: str | None
    votes: int | None = None

    @property
    def latin(self) -> str:
        return to_latin(normalize_text(self.name_en))

    @property
    def key(self) -> str:
        return comparable(self.name_en)


@dataclass
class Resolution:
    """What one header column resolved to."""

    column_index: int
    raw_header: str
    name: str
    party_abbr: str | None = None
    candidate_id: int | None = None
    method: str = "unresolved"
    score: float | None = None
    # Top-3 guesses with scores, for the review-queue item when this fails.
    candidates_considered: list[tuple[str, float]] = field(default_factory=list)
    # Which of the three conditions the best candidate failed, so the queue item
    # says why rather than only that something did not match.
    reject_reasons: list[str] = field(default_factory=list)

    @property
    def resolved(self) -> bool:
        return self.method != "unresolved"


def normalise_header(cell: str) -> str:
    """Master prompt 3.1 step 1.

    `normalize_text` already does NFC, whitespace collapsing and the Devanagari
    nukta/chandrabindu work (common/textnorm.py, the strongest module in the
    repo per the audit). Punctuation is removed except parentheses, which carry
    the party.
    """
    text = normalize_text(cell or "").replace("\n", " ")
    kept = []
    for ch in text:
        category = unicodedata.category(ch)
        # Drop punctuation and symbols, keep everything else. Testing for
        # `isalnum()` instead - which was the first thing I wrote here - deletes
        # every Devanagari combining mark, because a matra is category Mn and
        # not alphanumeric: 'कुल' came out as 'क ल' and no Devanagari header
        # matched anything. This is the same class of bug common/textnorm.py
        # exists to avoid.
        if category.startswith(("P", "S")) and ch not in "()":
            # A hyphen or full stop inside a name is a separator, not a letter,
            # so "Nirbhay Kr. Shahabadi" and "Nirbhay Kr Shahabadi" agree.
            kept.append(" ")
        else:
            kept.append(ch)
    return " ".join("".join(kept).split())


def _normalised_tokens(tokens: set[str]) -> frozenset[str]:
    """Normalise the token sets the same way a header cell is normalised.

    Without this, "No. of Rejected Votes" normalises to "No of Rejected Votes"
    and never matches its own entry in the set - the punctuation is stripped
    from the cell but not from the token it is being compared against.
    """
    return frozenset(normalise_header(token).lower() for token in tokens)


TAIL_TOKENS_NORMALISED = None   # built lazily; normalise_header is defined above
NOTA_TOKENS_NORMALISED = None


def _tail_set() -> frozenset[str]:
    global TAIL_TOKENS_NORMALISED
    if TAIL_TOKENS_NORMALISED is None:
        TAIL_TOKENS_NORMALISED = _normalised_tokens(TAIL_TOKENS)
    return TAIL_TOKENS_NORMALISED


def _nota_set() -> frozenset[str]:
    global NOTA_TOKENS_NORMALISED
    if NOTA_TOKENS_NORMALISED is None:
        NOTA_TOKENS_NORMALISED = _normalised_tokens(NOTA_TOKENS)
    return NOTA_TOKENS_NORMALISED


def is_tail_column(cell: str) -> bool:
    """Whether a header cell is a total rather than a candidate."""
    normalised = normalise_header(cell).lower()
    if not normalised:
        return True
    tokens = _tail_set()
    if normalised in tokens:
        return True
    # "Total of Valid Votes" and similar, where the cell opens with a tail
    # phrase. Prefix rather than substring: a candidate called "Total Ram" is
    # improbable, but "Kul" appearing inside a name is not.
    return any(normalised.startswith(token + " ") for token in tokens)


def is_nota(cell: str) -> bool:
    return normalise_header(cell).lower() in _nota_set()


def bracket_text(cell: str) -> str | None:
    """The text inside the last pair of brackets, if any."""
    normalised = normalise_header(cell)
    if "(" not in normalised or ")" not in normalised:
        return None
    start = normalised.rfind("(")
    end = normalised.rfind(")")
    if end < start:
        return None
    inner = normalised[start + 1 : end].strip()
    return inner or None


def strip_brackets(cell: str) -> str:
    normalised = normalise_header(cell)
    start = normalised.rfind("(")
    if start == -1:
        return normalised
    return normalised[:start].strip() or normalised


def resolve_column(
    column_index: int,
    raw_header: str,
    party_by_alias: dict[str, str],
    seeded: list[Candidate],
) -> Resolution:
    """Resolve one header cell. `party_by_alias` maps a normalised alias to an
    abbreviation; `seeded` is this election's published candidates."""
    name = strip_brackets(raw_header)
    resolution = Resolution(column_index=column_index, raw_header=raw_header, name=name)

    # Step 4 first: NOTA is a candidate row but never a contestant, and treating
    # it as a name to fuzzy-match would occasionally match a real candidate.
    if is_nota(raw_header):
        resolution.name = "NOTA"
        resolution.party_abbr = "NOTA"
        resolution.method = "nota"
        resolution.score = 1.0
        return resolution

    # Step 2: bracket text against party_alias, script-aware. Done first, and the
    # name is then used only to disambiguate: a party printed in the header is a
    # statement by the returning officer, whereas a name match is our inference,
    # so where both are available the printed one decides and the inference
    # narrows.
    inner = bracket_text(raw_header)
    pool = seeded
    if inner:
        abbr = party_by_alias.get(alias_key(inner)) or party_by_alias.get(inner.lower())
        if abbr:
            resolution.party_abbr = abbr
            resolution.method = "alias"
            resolution.score = 1.0
            # Only this party's candidates are now candidates for the name
            # match. If the party stood nobody we have a seeded row for, the
            # pool is empty rather than the whole field: matching a different
            # party's candidate would contradict the party the returning officer
            # printed, which is the one thing we are most sure of.
            by_party = [c for c in seeded if c.party_abbr == abbr]
            pool = by_party
            if by_party:
                if len(by_party) == 1:
                    # Nothing to disambiguate. The party is authoritative and
                    # there is exactly one person who stood for it, so the name
                    # does not need to clear any threshold - and should not have
                    # to, because this is the path that rescues a header whose
                    # spelling we do not recognise.
                    resolution.candidate_id = by_party[0].candidate_id
                    resolution.candidates_considered = [
                        (by_party[0].name_en,
                         round(jaro_winkler(comparable(name), by_party[0].key), 4))
                    ] if name else []
                    resolution.method = "alias+name"
                    return resolution

    # Step 3: fuzzy-match the name. Three conditions, all of which must hold -
    # see the threshold constants for why each exists.
    if pool and name:
        target = comparable(name)
        scored = sorted(
            ((c, jaro_winkler(target, c.key)) for c in pool),
            key=lambda pair: -pair[1],
        )
        resolution.candidates_considered = [(c.name_en, round(s, 4)) for c, s in scored[:3]]
        best, best_score = scored[0]
        second_score = scored[1][1] if len(scored) > 1 else 0.0
        margin = best_score - second_score
        first_part = _first_part_score(target, best.key)

        clears_threshold = best_score >= NAME_MATCH_THRESHOLD
        clears_margin = len(scored) == 1 or margin >= NAME_MATCH_MARGIN
        clears_first_part = first_part >= FIRST_PART_THRESHOLD

        if clears_threshold and clears_margin and clears_first_part:
            resolution.candidate_id = best.candidate_id
            resolution.score = round(best_score, 4)
            if resolution.party_abbr is None:
                resolution.party_abbr = best.party_abbr
                resolution.method = "name"
            else:
                resolution.method = "alias+name"
        else:
            reasons = []
            if not clears_threshold:
                reasons.append(f"best {best_score:.4f} < {NAME_MATCH_THRESHOLD}")
            if not clears_margin:
                reasons.append(
                    f"only {margin:.4f} ahead of {scored[1][0].name_en!r} "
                    f"(needs {NAME_MATCH_MARGIN})"
                )
            if not clears_first_part:
                reasons.append(
                    f"first name-part {first_part:.4f} < {FIRST_PART_THRESHOLD}"
                )
            resolution.reject_reasons = reasons
            log.warning("column %r does not resolve to %r: %s",
                        raw_header, best.name_en, "; ".join(reasons))

    # Step 5: an independent with no seeded row still resolves, to IND with a
    # distinct identity, so it competes individually.
    if resolution.method == "unresolved" and name and _looks_independent(raw_header):
        resolution.party_abbr = "IND"
        resolution.method = "independent"
        resolution.score = 1.0

    return resolution


def _first_part_score(target: str, candidate_key: str) -> float:
    """How well the first name-part matches.

    Both sides are already `comparable()` output, so they are lower-case,
    transliteration-neutral and whitespace-normalised.
    """
    left = target.split()
    right = candidate_key.split()
    if not left or not right:
        return 0.0
    return jaro_winkler(left[0], right[0])


def _looks_independent(cell: str) -> bool:
    inner = bracket_text(cell)
    if inner is None:
        return False
    return alias_key(inner) in {alias_key(t) for t in ("निर्दलीय", "निर्दल", "स्वतंत्र",
                                                       "IND", "Independent")}


def resolve_columns(
    headers: list[str],
    party_by_alias: dict[str, str],
    seeded: list[Candidate],
) -> list[Resolution]:
    """Resolve every candidate column. Tail columns are excluded by the caller."""
    return [
        resolve_column(index, header, party_by_alias, seeded)
        for index, header in enumerate(headers)
    ]


def unresolved(resolutions: list[Resolution]) -> list[Resolution]:
    return [r for r in resolutions if not r.resolved]


def review_payload(resolution: Resolution) -> dict:
    """The review-queue item for an unresolved column.

    Carries the raw header and the top three guesses with scores, so an operator
    can see what it nearly matched and decide whether to add an alias or correct
    the seeded name - rather than being told only that something failed.
    """
    return {
        "column_index": resolution.column_index,
        "raw_header": resolution.raw_header,
        "normalised": normalise_header(resolution.raw_header),
        "bracket_text": bracket_text(resolution.raw_header),
        "top_candidates": [
            {"name": name, "score": score}
            for name, score in resolution.candidates_considered
        ],
        "threshold": NAME_MATCH_THRESHOLD,
        "margin_required": NAME_MATCH_MARGIN,
        "first_part_threshold": FIRST_PART_THRESHOLD,
        "reject_reasons": resolution.reject_reasons,
        "fix": (
            "Add the spelling to db/seed/party_alias.csv and re-seed, or correct the "
            "candidate name in db/seed/ac_totals.csv, then re-run the parser."
        ),
    }


def load_party_aliases(cur) -> dict[str, str]:
    """alias -> party abbreviation, keyed the same way the seed wrote it."""
    cur.execute(
        "SELECT a.alias, p.abbr FROM party_alias a JOIN party p ON p.party_id = a.party_id"
    )
    mapping: dict[str, str] = {}
    for row in cur.fetchall():
        mapping[row["alias"].lower()] = row["abbr"]
        key = alias_key(row["alias"])
        if key:
            mapping[key] = row["abbr"]
    return mapping


def load_seeded_candidates(cur, election_id: int) -> list[Candidate]:
    """This election's published candidates, from `result_ac_total`.

    These are the reconciliation target: the AC-level figures a human entered
    from the ECI's published result. Matching header columns onto them is what
    makes the booth-sum-versus-published-total check possible at all, which is
    the check the README calls the gate to trust.
    """
    cur.execute(
        "SELECT c.candidate_id, c.name_en, p.abbr, t.value AS votes "
        "FROM candidate c "
        "LEFT JOIN party p ON p.party_id = c.party_id "
        "LEFT JOIN result_ac_total t ON t.candidate_id = c.candidate_id AND t.metric = 'votes' "
        "WHERE c.election_id = %s",
        (election_id,),
    )
    return [
        Candidate(
            candidate_id=row["candidate_id"],
            name_en=row["name_en"],
            party_abbr=row["abbr"],
            votes=row["votes"],
        )
        for row in cur.fetchall()
    ]
