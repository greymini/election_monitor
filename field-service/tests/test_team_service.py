import psycopg
import pytest

from pulse.team import service
from pulse.team.errors import Conflict, Invalid, NotFound
from pulse.team.service import Scope

AC = "ac-32"


def test_seeded_tree(conn, team):
    members = service.list_members(conn, AC)
    assert len(members) == 6
    principal = members[0]
    assert principal["is_principal"] and principal["parent_member_id"] is None
    worker = service.get_member(conn, AC, team["worker-a2"])
    assert worker["parent_member_id"] == team["incharge-a"]
    assert worker["scopes"] == [{"block_id": "blk-a", "area_id": "area-a2"}, {"block_id": "blk-a", "area_id": "area-a3"}]
    assert worker["telegram_linked_at"] is None


def test_one_principal_per_ac_service(conn, core):
    service.create_member(conn, core, AC, user_id="usr-principal", display_name="पहला", is_principal=True)
    with pytest.raises(Conflict, match="already has a principal"):
        service.create_member(conn, core, AC, user_id="usr-incharge-a", display_name="दूसरा", is_principal=True)


def test_one_principal_per_ac_index(conn):
    """The partial unique index itself, bypassing the service."""
    conn.execute(
        "INSERT INTO field.member (ac_id, user_id, display_name, is_principal) VALUES ('ac-32', 'u1', 'एक', true)"
    )
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(
            "INSERT INTO field.member (ac_id, user_id, display_name, is_principal) VALUES ('ac-32', 'u2', 'दो', true)"
        )
    # a principal in a different AC is fine
    conn.execute(
        "INSERT INTO field.member (ac_id, user_id, display_name, is_principal) VALUES ('ac-99', 'u3', 'तीन', true)"
    )


def test_parent_null_only_for_principal_constraint(conn):
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("INSERT INTO field.member (ac_id, user_id, display_name) VALUES ('ac-32', 'u9', 'नौ')")


def test_create_validates_against_core(conn, core, team):
    with pytest.raises(Invalid, match="unknown user"):
        service.create_member(conn, core, AC, user_id="usr-nobody", display_name="x", parent_member_id=team["principal"])
    with pytest.raises(Conflict, match="already a member"):
        service.create_member(conn, core, AC, user_id="usr-worker-a1", display_name="x", parent_member_id=team["principal"])


def test_scope_validation(core, pool):
    with pool.connection() as conn, conn.transaction():
        p = service.create_member(conn, core, AC, user_id="usr-principal", display_name="प", is_principal=True)
    for bad, msg in [
        (Scope(block_id="blk-zz"), "unknown block"),
        (Scope(area_id="area-zz"), "unknown area"),
        (Scope(block_id="blk-b", area_id="area-a1"), "not in block"),
        (Scope(), "needs a block_id or an area_id"),
    ]:
        with pytest.raises(Invalid, match=msg):
            with pool.connection() as conn, conn.transaction():
                service.create_member(
                    conn, core, AC, user_id="usr-worker-a1", display_name="क", parent_member_id=p["member_id"], scopes=[bad]
                )


def test_update_scopes_replace_the_set(conn, core, team):
    row = service.update_member(conn, core, AC, team["worker-a1"], {"scopes": [Scope(area_id="area-a3")]})
    assert row["scopes"] == [{"block_id": None, "area_id": "area-a3"}]


def test_reparent_cannot_create_cycle(conn, core, team):
    with pytest.raises(Invalid, match="loop"):
        service.update_member(conn, core, AC, team["incharge-a"], {"parent_member_id": team["worker-a1"]})
    with pytest.raises(Invalid, match="loop"):
        service.update_member(conn, core, AC, team["incharge-a"], {"parent_member_id": team["incharge-a"]})
    moved = service.update_member(conn, core, AC, team["worker-a1"], {"parent_member_id": team["incharge-b"]})
    assert moved["parent_member_id"] == team["incharge-b"]


def test_principal_cannot_get_a_parent(conn, core, team):
    with pytest.raises(Invalid):
        service.update_member(conn, core, AC, team["principal"], {"parent_member_id": team["incharge-a"]})


def test_unknown_member_and_field(conn, core, team):
    import uuid

    with pytest.raises(NotFound):
        service.update_member(conn, core, AC, uuid.uuid4(), {"display_name": "x"})
    with pytest.raises(Invalid, match="cannot change"):
        service.update_member(conn, core, AC, team["worker-a1"], {"is_principal": True})
    with pytest.raises(NotFound):  # member exists but in another AC
        service.get_member(conn, "ac-99", team["worker-a1"])


def test_deactivate_sets_timestamp_and_reactivate_clears(conn, core, team):
    row = service.update_member(conn, core, AC, team["worker-a1"], {"active": False})
    assert row["active"] is False and row["deactivated_at"] is not None
    row = service.update_member(conn, core, AC, team["worker-a1"], {"active": True})
    assert row["active"] is True and row["deactivated_at"] is None
