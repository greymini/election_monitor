from datetime import timedelta

from pulse.core_adapter import CoreAdapter, ac_by_number
from pulse.core_adapter.stub import StubCoreAdapter


def test_stub_satisfies_protocol(core):
    assert isinstance(core, CoreAdapter)


def test_seed_shape(core):
    ac = ac_by_number(core, 32)
    assert ac is not None
    assert len(core.list_blocks(ac.ac_id)) == 3
    assert len(core.list_areas(ac.ac_id)) == 8
    assert len(core.list_areas(ac.ac_id, "blk-a")) == 3
    roles = sorted(core.get_user(u).role for u in (
        "usr-principal", "usr-incharge-a", "usr-incharge-b", "usr-worker-a1", "usr-worker-a2", "usr-worker-b1"))
    assert roles == ["admin"] + ["rep"] * 5


def test_electors_missing_is_none_not_zero(core):
    assert core.electors_for_area("area-a1") == 4210
    assert core.electors_for_area("area-a3") is None
    assert core.electors_for_area("no-such-area") is None


def test_verify_token(core):
    token = core.issue_token("usr-principal")
    assert core.verify_token(token).user_id == "usr-principal"
    assert core.verify_token(token + "x") is None
    assert core.verify_token(core.issue_token("usr-principal", ttl=timedelta(seconds=-1))) is None
    other = StubCoreAdapter(jwt_secret="a-different-secret-0123456789abcdef")
    assert other.verify_token(token) is None
