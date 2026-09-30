"""Booth crosswalk scoring (LLD 4.4). The bands matter more than the numbers:
a wrong auto-accept silently corrupts every multi-year swing."""

from ingest.crosswalk import (
    AUTO_ACCEPT,
    REVIEW_FLOOR,
    SPLIT_FLOOR,
    Station,
    detect_splits,
    lineage_rows,
    match_stations,
    score_pair,
)


def S(ps, building, place="", part=None, uid=None):
    return Station(ps_number=ps, building=building, place=place, roll_part=part, booth_uid=uid)


def test_same_station_written_differently_auto_accepts():
    old = S(12, "प्रा०वि० चतरो", "चतरो", 12)
    new = S(15, "प्राथमिक विद्यालय चतरो", "चतरो", 12, uid="B0015")
    score, comp = score_pair(old, new)
    assert score >= AUTO_ACCEPT, (score, comp)
    assert comp["roll_part"] == 1.0


def test_part_qualifier_does_not_break_the_match():
    old = S(12, "प्रा०वि० चतरो (उत्तरी भाग)", "चतरो")
    new = S(12, "प्रा०वि० चतरो", "चतरो", uid="B0012")
    score, _ = score_pair(old, new)
    assert score >= AUTO_ACCEPT


def test_different_building_type_cannot_auto_accept():
    """A middle school at the same village is a different station and must never
    be auto-matched onto the primary school."""
    old = S(12, "प्रा०वि० चतरो", "चतरो", 12)
    new = S(12, "म० वि० चतरो", "चतरो", 12, uid="B0012")
    score, comp = score_pair(old, new)
    assert comp["type_mismatch_capped"] is True
    assert score < AUTO_ACCEPT


def test_unrelated_station_scores_below_review_floor():
    old = S(3, "प्रा०वि० चतरो", "चतरो")
    new = S(80, "पंचायत भवन मधुबन", "मधुबन", uid="B0080")
    score, _ = score_pair(old, new)
    assert score < REVIEW_FLOOR, score


def test_match_stations_assigns_bands():
    anchor = [
        S(1, "प्राथमिक विद्यालय चतरो", "चतरो", uid="B0001"),
        S(2, "मध्य विद्यालय पीरटांड", "पीरटांड", uid="B0002"),
        S(3, "पंचायत भवन मधुबन", "मधुबन", uid="B0003"),
    ]
    old = [
        S(1, "प्रा०वि० चतरो", "चतरो"),                  # clear match
        S(2, "उत्क्रमित म०वि० पीरटांड", "पीरटांड"),      # clear match
        S(9, "आंगनबाड़ी केंद्र नवाडीह", "नवाडीह"),        # nothing like it -> new
    ]
    matches = {m.ps_number: m for m in match_stations(old, anchor)}
    assert matches[1].method in {"exact", "fuzzy"} and matches[1].booth_uid == "B0001"
    assert matches[2].method in {"exact", "fuzzy"} and matches[2].booth_uid == "B0002"
    assert matches[9].method == "new" and matches[9].booth_uid is None


def test_split_detection():
    anchor = [S(1, "प्राथमिक विद्यालय चतरो", "चतरो", uid="B0001")]
    old = [
        S(1, "प्रा०वि० चतरो", "चतरो"),
        S(2, "प्रा०वि० चतरो", "चतरो"),   # same building appears twice -> split
    ]
    matches = match_stations(old, anchor)
    assert detect_splits(matches) == {"B0001"}


def test_runner_up_is_recorded_for_review():
    anchor = [
        S(1, "प्राथमिक विद्यालय चतरो", "चतरो", uid="B0001"),
        S(2, "प्राथमिक विद्यालय चतरा", "चतरा", uid="B0002"),
    ]
    m = match_stations([S(1, "प्रा०वि० चतरो", "चतरो")], anchor)[0]
    assert m.runner_up is not None
    assert m.runner_up[0] == "B0002"


# --------------------------------------------------------------------------
# The write path (master prompt 3.3). Scoring was always sound; this was not.
# --------------------------------------------------------------------------


class FakeCursor:
    """Records every statement, so the write path can be tested without a
    database. `apply_matches` is where B2, B3 and C16 lived, and none of them
    was reachable by a scoring test."""

    def __init__(self, election_id=7, area_id=3, uid_seq=None):
        self.statements: list[tuple[str, tuple]] = []
        self._election_id = election_id
        self._area_id = area_id
        self._uid_seq = iter(uid_seq or [f"32-B{n:04d}" for n in range(9001, 9100)])
        self._last = None

    def execute(self, sql, params=None):
        self.statements.append((" ".join(sql.split()), tuple(params or ())))
        lowered = sql.lower()
        if "from election where label" in lowered:
            self._last = {"election_id": self._election_id}
        elif "next_booth_uid" in lowered:
            self._last = {"uid": next(self._uid_seq)}
        elif "select area_id from booth" in lowered:
            self._last = {"area_id": self._area_id}
        else:
            self._last = None

    def fetchone(self):
        return self._last

    def written(self, table: str) -> list[tuple[str, tuple]]:
        return [s for s in self.statements if f"into {table}" in s[0].lower()]


def apply_with_fake(matches, splits, cursor):
    """Call apply_matches against a fake connection."""
    import contextlib
    from unittest import mock

    from ingest import crosswalk

    @contextlib.contextmanager
    def fake_connection():
        conn = mock.MagicMock()
        conn.cursor.return_value.__enter__ = lambda _self: cursor
        conn.cursor.return_value.__exit__ = lambda *a: False
        yield conn

    with mock.patch.dict("sys.modules", {"common.db": mock.MagicMock(connection=fake_connection)}):
        return crosswalk.apply_matches("VS-2019", ac_id=1, ac_number=32,
                                       matches=matches, splits=splits)


def _match(ps, uid, score, method):
    from ingest.crosswalk import Match

    return Match(ps_number=ps, booth_uid=uid, score=score, method=method, components={})


def test_a_review_band_match_writes_a_crosswalk_row():
    """B2, the most consequential defect in the write path.

    A station scoring between 0.65 and 0.85 used to write *only* a review_queue
    row and no booth_crosswalk row at all - and every materialized view reaches
    results through an inner join on that table, so its votes vanished from
    every rollup with no error. The HLD expects 10-20% of booths to need manual
    matching, so a multi-year comparison was built on 80-90% of the
    constituency while presenting itself as complete.
    """
    cursor = FakeCursor()
    stats = apply_with_fake([_match(12, "32-B0012", 0.70, "review")], set(), cursor)

    crosswalk_writes = cursor.written("booth_crosswalk")
    assert len(crosswalk_writes) == 1, "a review-band match must still get a crosswalk row"
    # With its real sub-threshold confidence, not a rounded-up one.
    assert 0.70 in crosswalk_writes[0][1]
    assert stats["review"] == 1


def test_a_review_band_row_is_marked_unreviewed():
    """The row carries the uncertainty rather than hiding it: the confidence
    column and the weak-crosswalk counter already existed for this."""
    cursor = FakeCursor()
    apply_with_fake([_match(12, "32-B0012", 0.70, "review")], set(), cursor)
    sql = cursor.written("booth_crosswalk")[0][0].lower()
    assert "false" in sql          # reviewed = false
    assert "reviewed = false" in sql   # and it will not overwrite a human's decision


def test_a_review_band_match_is_also_queued_with_an_explanation():
    cursor = FakeCursor()
    apply_with_fake([_match(12, "32-B0012", 0.70, "review")], set(), cursor)
    queued = cursor.written("review_queue")
    assert len(queued) == 1
    note = queued[0][1][-1]
    assert "0.700" in note
    assert "withheld" in note      # says what the consequence is


def test_new_booth_uids_come_from_the_per_ac_sequence():
    """B3. The old code minted B9001, B9002 ... from a counter that restarted at
    zero on every invocation, so crosswalking a second historic election reused
    the first's UIDs - and because the booth insert was ON CONFLICT DO NOTHING,
    the new crosswalk row pointed at a booth created for a completely different
    polling station. Two unrelated stations' votes were then summed onto one
    booth_uid."""
    cursor = FakeCursor()
    apply_with_fake([_match(99, None, 0.20, "new")], set(), cursor)

    assert any("next_booth_uid" in s[0] for s in cursor.statements), (
        "a new booth must be minted from the sequence, not from a local counter"
    )
    booth_writes = cursor.written("booth")
    assert booth_writes
    assert "32-B9001" in booth_writes[0][1]


def test_two_crosswalk_runs_cannot_collide_on_a_uid():
    """The concrete B3 scenario: crosswalk one election, then another."""
    shared = iter([f"32-B{n:04d}" for n in range(9001, 9010)])
    first = FakeCursor(uid_seq=shared)
    second = FakeCursor(uid_seq=shared)

    apply_with_fake([_match(99, None, 0.2, "new")], set(), first)
    apply_with_fake([_match(98, None, 0.2, "new")], set(), second)

    uid_one = [p for p in first.written("booth")[0][1] if isinstance(p, str) and "-B" in p][0]
    uid_two = [p for p in second.written("booth")[0][1] if isinstance(p, str) and "-B" in p][0]
    assert uid_one != uid_two


def test_a_new_booth_is_created_inactive():
    """It has no area yet, so it must not appear on the map as though placed."""
    cursor = FakeCursor()
    apply_with_fake([_match(99, None, 0.2, "new")], set(), cursor)
    assert "false" in cursor.written("booth")[0][0].lower()


def test_review_queue_inserts_are_deduped():
    """C16: the runbook tells operators to re-run the crosswalk after fixes, and
    these inserts had no dedupe, so the queue doubled every time."""
    cursor = FakeCursor()
    apply_with_fake([_match(12, "32-B0012", 0.70, "review")], set(), cursor)
    sql = cursor.written("review_queue")[0][0].lower()
    assert "on conflict (kind, ref)" in sql
    # And a re-run must not reopen something already closed.
    assert "status = 'open'" in sql


# --------------------------------------------------------------------------
# Split and merge lineage
# --------------------------------------------------------------------------


def test_splits_are_detected_above_the_split_floor():
    """0.75, lower than auto-accept, because a split station's building name is
    often abbreviated differently on each of the new rows."""
    matches = [_match(1, "32-B0001", 0.80, "review"), _match(2, "32-B0001", 0.78, "review")]
    assert detect_splits(matches) == {"32-B0001"}


def test_a_weak_second_match_is_not_a_split():
    matches = [_match(1, "32-B0001", 0.95, "fuzzy"), _match(2, "32-B0001", 0.40, "new")]
    assert detect_splits(matches) == set()


def test_lineage_rows_weight_an_equal_split():
    """No published split ratio exists, so an equal split is the only
    defensible assumption - and it is recorded as an assumption so a correction
    is a data edit rather than a code change."""
    matches = [_match(1, "32-B0001", 0.80, "review"), _match(2, "32-B0001", 0.78, "review")]
    rows = lineage_rows(matches, {"32-B0001"})

    assert len(rows) == 2
    assert all(r["weight"] == 0.5 for r in rows)
    assert sum(r["weight"] for r in rows) == 1.0
    assert all("no published ratio" in r["note"] for r in rows)


def test_lineage_rows_are_written():
    cursor = FakeCursor()
    matches = [_match(1, "32-B0001", 0.80, "review"), _match(2, "32-B0001", 0.78, "review")]
    stats = apply_with_fake(matches, {"32-B0001"}, cursor)

    assert stats["lineage"] == 2
    assert len(cursor.written("booth_lineage")) == 2


def test_a_split_match_is_recorded_as_such_on_the_crosswalk_row():
    cursor = FakeCursor()
    matches = [_match(1, "32-B0001", 0.80, "review"), _match(2, "32-B0001", 0.78, "review")]
    apply_with_fake(matches, {"32-B0001"}, cursor)
    assert any("split" in s[1] for s in cursor.written("booth_crosswalk"))


def test_the_split_floor_sits_between_the_review_floor_and_auto_accept():
    assert REVIEW_FLOOR < SPLIT_FLOOR < AUTO_ACCEPT


# --------------------------------------------------------------------------
# roll_part (C10)
# --------------------------------------------------------------------------


def test_the_roll_part_term_contributes_when_both_sides_carry_it():
    """C10: `booth` did not carry roll_part, so the documented 0.20 term never
    contributed and every score was really 62.5% building / 37.5% place."""
    old = Station(ps_number=12, building="प्रा०वि० चतरो", place="चतरो", roll_part=12)
    same = Station(ps_number=15, building="प्राथमिक विद्यालय चतरो", place="चतरो",
                   roll_part=12, booth_uid="32-B0015")
    different = Station(ps_number=15, building="प्राथमिक विद्यालय चतरो", place="चतरो",
                        roll_part=44, booth_uid="32-B0015")

    with_match, comp_match = score_pair(old, same)
    with_mismatch, comp_mismatch = score_pair(old, different)

    assert comp_match["roll_part"] == 1.0
    assert comp_mismatch["roll_part"] == 0.0
    assert with_match > with_mismatch, "the roll-part term must actually move the score"
