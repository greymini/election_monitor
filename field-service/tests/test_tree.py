"""is_in_subtree: the helper advances will use to check that money only moves down the tree."""
from pulse.team.tree import is_in_subtree, subtree_ids


def test_descendants_are_in_subtree(conn, team):
    assert is_in_subtree(conn, team["principal"], team["incharge-a"])
    assert is_in_subtree(conn, team["principal"], team["worker-a1"])  # grandchild
    assert is_in_subtree(conn, team["incharge-a"], team["worker-a2"])


def test_self_is_not_in_own_subtree(conn, team):
    assert not is_in_subtree(conn, team["incharge-a"], team["incharge-a"])


def test_ancestors_siblings_and_cousins_are_not(conn, team):
    assert not is_in_subtree(conn, team["worker-a1"], team["incharge-a"])  # upward
    assert not is_in_subtree(conn, team["incharge-a"], team["principal"])
    assert not is_in_subtree(conn, team["worker-a1"], team["worker-a2"])  # sibling
    assert not is_in_subtree(conn, team["incharge-a"], team["worker-b1"])  # other branch
    assert not is_in_subtree(conn, team["incharge-a"], team["incharge-b"])


def test_leaf_has_empty_subtree(conn, team):
    assert subtree_ids(conn, team["worker-b1"]) == set()
    assert subtree_ids(conn, team["incharge-a"]) == {team["worker-a1"], team["worker-a2"]}
    assert len(subtree_ids(conn, team["principal"])) == 5
