"""Electoral roll parser: counts only.

COMPLIANCE, and the reason this module is shaped the way it is (HLD 5, LLD 12):

    No voter name, EPIC number, relative's name, house number or address is
    ever returned from this module, written to the database, or logged.

The parse functions return `RollCounts`, which contains integers and a surname
histogram and nothing else. Names exist only as locals inside the scanning loop
and are dropped when it ends. tests/test_roll_privacy.py asserts this on
realistic input, and that test is the reason the API surface is a dataclass of
counters rather than a list of records.

    python -m ingest.parse_roll raw/roll_2026_ps001.pdf --revision "2026-SSR" --dry-run
    python -m ingest.parse_roll raw/supplement_2026_10.pdf --revision "2026-SUP-1" --supplement --load
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from common.logging_setup import get_logger
from common.textnorm import last_token, normalize_block, normalize_digits
from ingest.acscope import AmbiguousScope, ElectionScope, add_ac_argument, resolve_election
from ingest.documents import (
    DocumentNotFound,
    add_document_arguments,
    advance_status,
    open_document,
)

log = get_logger(__name__)

# EPIC formats seen in Jharkhand rolls: the current 3-letter + 7-digit form and
# the older slash-separated form. Matched only to COUNT entries.
EPIC_RE = re.compile(r"\b(?:[A-Z]{3}\d{7}|[A-Z]{2}/\d{2}/\d{3}/\d{6})\b")

# A relative's name is labelled "पिता का नाम" / "पति का नाम", which also contains
# the word "नाम". Those spans are stripped BEFORE the elector name is read, so a
# father's or husband's surname is never counted as an elector's.
RELATIVE_RE = re.compile(
    r"(?:पिता|पति|माता|Father|Husband|Mother)\s*(?:का\s*)?(?:नाम|Name)\s*[:：]"
    r".*?(?=(?:मकान|House|आयु|Age|लिंग|Gender|क्रम)|$)",
    re.IGNORECASE,
)

NAME_RE = re.compile(
    r"(?:नाम|Name)\s*[:：]\s*(.+?)"
    r"(?=\s{2,}|(?:पिता|पति|माता|मकान|House|Father|Husband|Age|आयु|लिंग|Gender)|$)",
    re.IGNORECASE,
)
AGE_RE = re.compile(r"(?:आयु|उम्र|Age)\s*[:：]?\s*(\d{1,3})", re.IGNORECASE)
GENDER_RE = re.compile(
    r"(?:लिंग|Gender)\s*[:：]?\s*(पुरुष|महिला|अन्य|Male|Female|Other|M|F|O)"
    #  is useless after a Devanagari matra - "महिला" ends in one.
    r"(?![\w\u0900-\u097f])",
    re.IGNORECASE,
)

PS_NUMBER_RE = re.compile(
    r"(?:मतदान\s*केन्?द्र\s*(?:सं|संख्या)|Polling\s*Station\s*(?:No|Number))\s*[.:：]?\s*(\d{1,3})",
    re.IGNORECASE,
)
PART_NUMBER_RE = re.compile(
    r"(?:भाग\s*संख्या|Part\s*(?:No|Number))\s*[.:：]?\s*(\d{1,4})", re.IGNORECASE
)

# Supplement section headings
SECTION_PATTERNS = {
    "additions": [r"परिवर्धन", r"ADDITION", r"नये?\s*मतदाता", r"SUPPLEMENT.*ADDITION"],
    "deletions": [r"विलोपन", r"DELETION", r"अपमार्जन", r"हटाये?\s*गये"],
    "modifications": [r"संशोधन", r"MODIFICATION", r"CORRECTION"],
}

DELETION_REASONS = {
    "death": [r"मृत", r"मृत्यु", r"DEAD", r"DEATH", r"EXPIRED", r"\bE\b"],
    "shifted": [r"स्थाना?न्?तरित", r"स्थानांतरण", r"SHIFT", r"SHIFTED", r"\bS\b"],
}

MALE_TOKENS = {"पुरुष", "male", "m"}
FEMALE_TOKENS = {"महिला", "female", "f"}


@dataclass
class RollCounts:
    """Everything this module is allowed to produce. Integers and a surname
    histogram - no personal data of any kind."""

    ps_number: int | None = None
    roll_part: int | None = None
    electors: int = 0
    male: int = 0
    female: int = 0
    other: int = 0
    age_18_19: int = 0
    age_20_29: int = 0
    age_30_39: int = 0
    age_40_49: int = 0
    age_50_59: int = 0
    age_60p: int = 0
    surnames: Counter = field(default_factory=Counter)

    # Supplement-only
    additions: int = 0
    deletions: int = 0
    modifications: int = 0
    add_18_19: int = 0
    add_female: int = 0
    add_male: int = 0
    del_death: int = 0
    del_shifted: int = 0
    del_other: int = 0

    def add_age(self, age: int | None, prefix: str = "age") -> None:
        if age is None:
            return
        if prefix == "add":
            if 18 <= age <= 19:
                self.add_18_19 += 1
            return
        if age < 18:
            return
        if age <= 19:
            self.age_18_19 += 1
        elif age <= 29:
            self.age_20_29 += 1
        elif age <= 39:
            self.age_30_39 += 1
        elif age <= 49:
            self.age_40_49 += 1
        elif age <= 59:
            self.age_50_59 += 1
        else:
            self.age_60p += 1


def _gender_bucket(token: str | None) -> str:
    if not token:
        return "other"
    t = token.strip().lower()
    if t in MALE_TOKENS:
        return "male"
    if t in FEMALE_TOKENS:
        return "female"
    return "other"


def _section_of(line: str) -> str | None:
    for section, patterns in SECTION_PATTERNS.items():
        if any(re.search(p, line, re.IGNORECASE) for p in patterns):
            return section
    return None


def _deletion_reason(line: str) -> str:
    for reason, patterns in DELETION_REASONS.items():
        if any(re.search(p, line, re.IGNORECASE) for p in patterns):
            return reason
    return "other"


def _attrs_for(lines: list[str], i: int, n_entries: int) -> tuple[list, list, list]:
    """Gender, age and name tokens for the entries on line `i`.

    Most roll layouts put one entry per line, but some wrap across two. Read the
    line itself first and widen FORWARD only when it does not yield enough
    values - widening backwards would attribute the previous elector's gender or
    age to this one, silently corrupting every age-band and gender count.
    """
    line = lines[i]
    genders = GENDER_RE.findall(line)
    ages = [int(a) for a in AGE_RE.findall(line)]
    names = NAME_RE.findall(RELATIVE_RE.sub(" ", line))

    if min(len(genders), len(ages), len(names)) < n_entries:
        window = " ".join(lines[i: i + 2])
        if len(genders) < n_entries:
            genders = GENDER_RE.findall(window)
        if len(ages) < n_entries:
            ages = [int(a) for a in AGE_RE.findall(window)]
        if len(names) < n_entries:
            names = NAME_RE.findall(RELATIVE_RE.sub(" ", window))
    return genders, ages, names


def scan_mother_roll(text: str) -> RollCounts:
    """Count electors, gender and age bands, and histogram surnames.

    One entry per EPIC number. Attributes are taken from the same line, or the
    line before, since roll layouts wrap entries differently.
    """
    counts = RollCounts()
    text = normalize_digits(normalize_block(text).replace("\r", ""))

    m = PS_NUMBER_RE.search(text)
    if m:
        counts.ps_number = int(m.group(1))
    m = PART_NUMBER_RE.search(text)
    if m:
        counts.roll_part = int(m.group(1))

    lines = text.split("\n")
    for i, line in enumerate(lines):
        n_entries = len(EPIC_RE.findall(line))
        if n_entries == 0:
            continue
        counts.electors += n_entries
        genders, ages, names = _attrs_for(lines, i, n_entries)

        for k in range(n_entries):
            bucket = _gender_bucket(genders[k] if k < len(genders) else None)
            setattr(counts, bucket, getattr(counts, bucket) + 1)
            counts.add_age(ages[k] if k < len(ages) else None)
            if k < len(names):
                surname = last_token(names[k])
                if surname:
                    counts.surnames[surname] += 1
            # `names` goes out of scope with this loop. Nothing here is kept.

    return counts


def scan_supplement(text: str) -> RollCounts:
    """Count additions, deletions and modifications per section (LLD 4.3)."""
    counts = RollCounts()
    text = normalize_digits(normalize_block(text).replace("\r", ""))

    m = PS_NUMBER_RE.search(text)
    if m:
        counts.ps_number = int(m.group(1))
    m = PART_NUMBER_RE.search(text)
    if m:
        counts.roll_part = int(m.group(1))

    section: str | None = None
    lines = text.split("\n")
    for i, line in enumerate(lines):
        heading = _section_of(line)
        if heading:
            section = heading
            continue
        if section is None:
            continue

        n_entries = len(EPIC_RE.findall(line))
        if n_entries == 0:
            continue
        window = " ".join(lines[i: i + 2])

        if section == "additions":
            counts.additions += n_entries
            genders, ages, _names = _attrs_for(lines, i, n_entries)
            for k in range(n_entries):
                bucket = _gender_bucket(genders[k] if k < len(genders) else None)
                if bucket == "female":
                    counts.add_female += 1
                elif bucket == "male":
                    counts.add_male += 1
                counts.add_age(ages[k] if k < len(ages) else None, prefix="add")
        elif section == "deletions":
            counts.deletions += n_entries
            reason = _deletion_reason(window)
            if reason == "death":
                counts.del_death += n_entries
            elif reason == "shifted":
                counts.del_shifted += n_entries
            else:
                counts.del_other += n_entries
        else:
            counts.modifications += n_entries

    return counts


def scan_pdf(pdf_path: Path, supplement: bool = False, force: bool = False) -> list[RollCounts]:
    """Scan a roll PDF, splitting it per polling station.

    A single PDF often holds many parts, so pages are grouped by the PS number
    printed in their header, and one RollCounts is produced per station.

    `cache=False` is not optional here and must never be removed. Audit C3: with
    the cache on, every page's complete text - names, EPIC numbers, relatives'
    names, house numbers, ages - was written to OCR_DIR as plaintext JSON and
    kept indefinitely, while discard_raw deleted the source PDF. The system
    destroyed the evidence and retained the personal data.

    `tests/test_roll_privacy.py` asserts that nothing lands under OCR_DIR when
    this runs, so removing the flag fails a test rather than silently
    reintroducing the leak.
    """
    from ingest.extract_pdf import extract_document

    # tables=False: a roll has no ruling lines, so the lattice pass returns
    # nothing and roughly doubles the time. scan_mother_roll and scan_supplement
    # read page.text and nothing else.
    pages = extract_document(pdf_path, force=force, cache=False, tables=False)
    groups: dict[int | None, list[str]] = {}
    current: int | None = None
    for page in pages:
        m = PS_NUMBER_RE.search(normalize_digits(page.text))
        if m:
            current = int(m.group(1))
        groups.setdefault(current, []).append(page.text)

    scan = scan_supplement if supplement else scan_mother_roll
    results: list[RollCounts] = []
    for ps_number, texts in groups.items():
        counts = scan("\n".join(texts))
        if counts.ps_number is None:
            counts.ps_number = ps_number
        if counts.ps_number is not None:
            results.append(counts)
    return results


# --------------------------------------------------------------------------
# Loading - aggregates only
# --------------------------------------------------------------------------

def _booth_for_ps(cur, scope: ElectionScope, ps_number: int) -> str | None:
    """The booth a polling-station number maps to, within this AC.

    Both lookups were AC-blind: the crosswalk join matched on `election.label`,
    which six constituencies share, and the fallback matched
    `current_ps_number` across the whole `booth` table. Either could return
    another constituency's booth, and a roll snapshot written against it would
    put one AC's electorate on another AC's booth with no constraint to stop it.
    """
    cur.execute(
        "SELECT booth_uid FROM booth_crosswalk WHERE election_id = %s AND ps_number = %s",
        (scope.election_id, ps_number),
    )
    row = cur.fetchone()
    if row:
        return row["booth_uid"]
    cur.execute(
        "SELECT booth_uid FROM booth WHERE ac_id = %s AND current_ps_number = %s",
        (scope.ac_id, ps_number),
    )
    row = cur.fetchone()
    return row["booth_uid"] if row else None


def load(results: list[RollCounts], revision_label: str, revision_date: str,
         supplement: bool, scope: ElectionScope,
         is_post_sir: bool = False, write_surnames: bool = True,
         source_doc: str | None = None, link_election: str | None = None) -> dict:
    """Write per-booth counts for one roll revision. One transaction.

    `ac_id` is written on `roll_revision`, `roll_snapshot` and `roll_change`,
    all three of which 0014 made NOT NULL, and the revision upsert now targets
    `(ac_id, label)`. The old `ON CONFLICT (label)` named a constraint 0014
    dropped, so this function raised `InvalidColumnReference` on its first
    statement against the current schema - B11 was fixed in the migration and
    not in the loader.
    """
    from common.db import connection

    stats = {"snapshots": 0, "changes": 0, "surname_rows": 0, "unmatched_ps": 0}
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO roll_revision (ac_id, revision_date, label, is_post_sir, is_mother, "
            "source_doc) VALUES (%s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (ac_id, label) DO UPDATE SET "
            "revision_date = EXCLUDED.revision_date, is_post_sir = EXCLUDED.is_post_sir, "
            "source_doc = EXCLUDED.source_doc "
            "RETURNING revision_id",
            (scope.ac_id, revision_date, revision_label, is_post_sir, not supplement,
             source_doc),
        )
        revision_id = cur.fetchone()["revision_id"]

        if link_election and not supplement:
            # B1, the half that was never closed. `election_roll_link` is what
            # tells the metrics layer which roll revision an election was fought
            # on: `mv_result_booth_wide` takes its turnout denominator from the
            # linked revision's snapshot, and `mv_new_voter_share` takes both
            # ends of its window from the link. Nothing in this repository ever
            # inserted a row into it - the audit noted the consequence (0
            # additions everywhere) and the table stayed empty because it had no
            # writer at all. The operator loading a mother roll is the only one
            # who knows which contest it is the roll for, so that is where the
            # link belongs.
            link_scope = resolve_election(cur, link_election, ac_number=scope.ac_number)
            cur.execute(
                "INSERT INTO election_roll_link (election_id, revision_id) VALUES (%s, %s) "
                "ON CONFLICT (election_id) DO UPDATE SET revision_id = EXCLUDED.revision_id",
                (link_scope.election_id, revision_id),
            )
            log.info("linked roll revision %s to %s", revision_label, link_scope)

        surname_totals: dict[str, Counter] = {}
        # Electors per booth, for the surname estimator's denominator. C14: the
        # share of a community is a share of the electorate, not of the names the
        # dictionary happened to recognise.
        booth_electors: dict[str, int] = {}

        for c in results:
            booth_uid = _booth_for_ps(cur, scope, c.ps_number) if c.ps_number else None
            if booth_uid is None:
                stats["unmatched_ps"] += 1
                cur.execute(
                    "INSERT INTO review_queue (kind, ref, ac_id, payload, note) VALUES "
                    "('roll_section', %s, %s, %s, %s) "
                    "ON CONFLICT (kind, ref) DO NOTHING",
                    (f"{revision_label}#AC{scope.ac_number}#PS{c.ps_number}", scope.ac_id,
                     "{}",
                     f"roll section for PS {c.ps_number} has no booth in the crosswalk"),
                )
                continue

            if supplement:
                cur.execute(
                    "INSERT INTO roll_change (revision_id, ac_id, booth_uid, additions, deletions, "
                    "modifications, add_18_19, add_female, add_male, del_death, del_shifted, "
                    "del_other, source_doc) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (revision_id, booth_uid) DO UPDATE SET "
                    "additions = EXCLUDED.additions, deletions = EXCLUDED.deletions, "
                    "modifications = EXCLUDED.modifications, add_18_19 = EXCLUDED.add_18_19, "
                    "add_female = EXCLUDED.add_female, add_male = EXCLUDED.add_male, "
                    "del_death = EXCLUDED.del_death, del_shifted = EXCLUDED.del_shifted, "
                    "del_other = EXCLUDED.del_other, source_doc = EXCLUDED.source_doc",
                    (revision_id, scope.ac_id, booth_uid, c.additions, c.deletions,
                     c.modifications, c.add_18_19, c.add_female, c.add_male, c.del_death,
                     c.del_shifted, c.del_other, source_doc),
                )
                stats["changes"] += 1
            else:
                cur.execute(
                    "INSERT INTO roll_snapshot (revision_id, ac_id, booth_uid, electors, male, "
                    "female, other, age_18_19, age_20_29, age_30_39, age_40_49, age_50_59, "
                    "age_60p, source_doc) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (revision_id, booth_uid) DO UPDATE SET electors = EXCLUDED.electors, "
                    "male = EXCLUDED.male, female = EXCLUDED.female, other = EXCLUDED.other, "
                    "age_18_19 = EXCLUDED.age_18_19, age_20_29 = EXCLUDED.age_20_29, "
                    "age_30_39 = EXCLUDED.age_30_39, age_40_49 = EXCLUDED.age_40_49, "
                    "age_50_59 = EXCLUDED.age_50_59, age_60p = EXCLUDED.age_60p, "
                    "source_doc = EXCLUDED.source_doc",
                    (revision_id, scope.ac_id, booth_uid, c.electors, c.male, c.female,
                     c.other, c.age_18_19, c.age_20_29, c.age_30_39, c.age_40_49,
                     c.age_50_59, c.age_60p, source_doc),
                )
                stats["snapshots"] += 1
                booth_electors[booth_uid] = c.electors
                if write_surnames and c.surnames:
                    surname_totals[booth_uid] = c.surnames

        if write_surnames and surname_totals:
            from analytics.caste_estimate import write_surname_estimates

            stats["surname_rows"] = write_surname_estimates(
                cur, surname_totals, scope.ac_id, electors=booth_electors)

    return stats


def discard_raw(pdf_path: Path) -> None:
    """Retain the source roll PDF, restricted, unless RETAIN_RAW_ROLLS is off.

    C13, and the other half of C3. The default was to delete the PDF, which -
    combined with the page cache that kept its full text - meant the system
    destroyed the only auditable original while retaining the personal data
    extracted from it. You could not re-parse to verify a count or fix a parser
    bug, and you could not show a regulator what the source said, but you were
    still holding the names.

    RETAIN_RAW_ROLLS now defaults true and the file is left at 0600 under a 0700
    directory, which is what LLD 12 actually describes: "stored in raw/ with
    filesystem permissions to worker only". Deleting it remains possible for an
    operator who wants it gone, and is logged.
    """
    from common.config import get_settings

    if get_settings().retain_raw_rolls:
        _restrict_roll_file(pdf_path)
        return
    try:
        pdf_path.unlink()
        log.warning("deleted raw roll %s (RETAIN_RAW_ROLLS=false). The auditable "
                    "original is gone; a count cannot be re-verified from source.",
                    pdf_path.name)
    except OSError as exc:
        log.warning("could not delete %s: %s", pdf_path.name, exc)


def _restrict_roll_file(pdf_path: Path) -> None:
    """0600 on the file, 0700 on its directory. Best effort: os.chmod only
    toggles the read-only bit on Windows, and the compliance target is the Linux
    host."""
    import os

    for path, mode in ((pdf_path, 0o600), (pdf_path.parent, 0o700)):
        try:
            os.chmod(path, mode)
        except (OSError, NotImplementedError) as exc:
            log.debug("could not restrict %s: %s", path, exc)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Parse an electoral roll into per-booth counts")
    # A path, --doc SHA256 or --key STORAGE_KEY. For a roll, common/storage.py
    # refuses any backend but local, so --doc will only ever resolve to a file
    # on this host.
    add_document_arguments(ap)
    ap.add_argument("--revision", required=True, help="revision label, e.g. 2026-SSR")
    ap.add_argument("--date", required=True, help="revision date, YYYY-MM-DD")
    ap.add_argument("--supplement", action="store_true", help="this is a supplementary list")
    ap.add_argument("--post-sir", action="store_true", help="revision follows a Special Intensive Revision")
    ap.add_argument("--election", default="VS-2024",
                    help="election whose crosswalk maps PS numbers to booths")
    add_ac_argument(ap)
    ap.add_argument("--link-election", metavar="LABEL",
                    help="record this revision as the roll the named election was "
                         "fought on (election_roll_link). Turnout and new-voter share "
                         "are NULL until a mother roll is linked. Mother rolls only.")
    ap.add_argument("--no-surnames", action="store_true", help="skip surname aggregation")
    ap.add_argument("--load", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    kind = "roll_supplement" if args.supplement else "roll_mother"
    try:
        with open_document(pdf=args.pdf, doc=args.doc, key=args.key,
                           backend=args.backend, kind=kind) as (path, provenance):
            return _run(args, path, provenance, kind)
    except DocumentNotFound as exc:
        log.error("%s", exc)
        return 2


def _run(args, path: Path, provenance: dict, kind: str) -> int:
    results = scan_pdf(path, supplement=args.supplement, force=args.force)
    if not results:
        log.error("no polling-station sections found in %s", path.name)
        return 1

    if args.supplement:
        log.info("%s: %d section(s), %d addition(s), %d deletion(s), %d modification(s)",
                 path.name, len(results), sum(r.additions for r in results),
                 sum(r.deletions for r in results), sum(r.modifications for r in results))
    else:
        total = sum(r.electors for r in results)
        log.info("%s: %d section(s), %d elector(s) counted (M %d / F %d / O %d)",
                 path.name, len(results), total, sum(r.male for r in results),
                 sum(r.female for r in results), sum(r.other for r in results))
        matched = sum(sum(r.surnames.values()) for r in results)
        log.info("surname tokens seen: %d (%.1f%% of electors)",
                 matched, 100.0 * matched / total if total else 0.0)

    if args.dry_run or not args.load:
        log.info("dry run - nothing written. Re-run with --load to write.")
        return 0

    # C3 is closed, but a host that ran the old code still has roll text under
    # OCR_DIR and it does not expire. Loading more roll data onto a host that is
    # already leaking is the wrong order of operations, so the load refuses
    # until the disk is clean.
    from ingest.validate import check_privacy_filesystem

    for check in check_privacy_filesystem():
        if not check.passed:
            log.error("refusing to load: %s", check.detail)
            for row in (check.rows or [])[:10]:
                log.error("  %s", row)
            log.error("Run scripts/purge_roll_cache.py --delete, then retry.")
            return 3

    from common.db import cursor

    try:
        with cursor() as cur:
            scope = resolve_election(cur, args.election, ac_number=args.ac)
    except (AmbiguousScope, ValueError) as exc:
        log.error("%s", exc)
        return 2
    log.info("loading into %s", scope)

    if args.link_election and args.supplement:
        log.error("--link-election names the roll an election was fought on, which is a "
                  "mother roll. A supplement cannot be that roll.")
        return 2

    stats = load(results, args.revision, args.date, args.supplement, scope,
                 is_post_sir=args.post_sir, write_surnames=not args.no_surnames,
                 source_doc=path.name, link_election=args.link_election)
    log.info("loaded: %(snapshots)d snapshot(s), %(changes)d change row(s), "
             "%(surname_rows)d surname estimate row(s), %(unmatched_ps)d unmatched section(s)", stats)
    if provenance.get("sha256"):
        try:
            advance_status(provenance["sha256"], "loaded", actor="ingest.parse_roll")
        except Exception as exc:
            log.warning("could not set parse_status: %s", exc)
    discard_raw(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
