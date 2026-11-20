from pulse.team.names import matches

PEOPLE = [
    {"member_id": 1, "display_name": "रमेश महतो", "user_id": "usr-incharge-a"},     # core name: Demo In-charge A
    {"member_id": 2, "display_name": "रमेश साव", "user_id": "usr-incharge-b"},
    {"member_id": 3, "display_name": "सुनीता देवी", "user_id": "usr-worker-a1"},
]


def _ids(q, core):
    return [p["member_id"] for p in matches(q, PEOPLE, core)]


def test_one_match(core):
    assert _ids("रमेश महतो", core) == [1]
    assert _ids("सुनीता", core) == [3]
    assert _ids("रमेश मह", core) == [1]          # word starts
    assert _ids("in-charge a", core) == [1]     # core display name, Roman


def test_several_matches(core):
    assert _ids("रमेश", core) == [1, 2]
    assert _ids("demo", core) == [1, 2, 3]


def test_no_match(core):
    assert _ids("ramesh", core) == []           # no transliteration: never guess across scripts
    assert _ids("महेश", core) == []
    assert _ids("", core) == []
