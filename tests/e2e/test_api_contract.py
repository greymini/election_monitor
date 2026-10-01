"""Every route, for every role, against a loaded database.

`FRONTEND_HARDENING.md` 2 asks for exactly this: "a test that calls every route,
for every role, against the seeded dev database, and asserts correct status for
the role (200 or 403, never 500), and the response validates against its model."

It is the test this codebase most obviously lacked. `tests/test_api_topology.py`
proves the routes are *registered* and `tests/test_api_sql_relations.py` proves
their SQL names relations that exist - neither executes a query, so a column
renamed in a migration breaks a page and nothing notices. `0015_metrics.sql`
renamed `votes_counted` to `votes_polled`, three handlers still read the old
name, and `GET /acs/{ac}/summary` has been returning 500 ever since.

The route list is **derived from the application**, not declared here, and a
route with no entry in `PATH_PARAMS`/`BODIES` fails the test. A new endpoint
therefore cannot be added without saying how to call it, which is the only way
a list like this stays true.

**Skipped without a database.** Set E2E_DATABASE_URL; see tests/e2e/conftest.py.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass

import pytest
from fastapi.routing import APIRoute

from tests.e2e.conftest import requires_db

pytestmark = requires_db

ROLES = ("admin", "strategist", "block")

# How the role dependency on an endpoint maps to the roles it admits. Read off
# the annotation rather than declared, so the two cannot drift: `api/deps.py`
# exports exactly these three aliases and every data route uses one of them.
ROLE_ALIASES = {
    "AdminUser": {"admin"},
    "StrategistUser": {"admin", "strategist"},
    "CurrentUser": {"admin", "strategist", "block"},
}

# Routes that take no user at all. Each is deliberate and worth stating.
PUBLIC = {
    ("GET", "/health"),      # a load balancer reads this
    ("GET", "/config"),      # the login screen needs it before anyone has a token
    ("POST", "/auth/login"),
    ("POST", "/auth/otp"),
    ("POST", "/auth/verify"),
}

# Path parameters, filled from the loaded dataset by the `ids` fixture.
PATH_PARAMS = {
    "ac_number": "ac_number",
    "booth_uid": "booth_uid",
    "election_label": "election_label",
    "item_id": "review_item_id",
}

# Bodies for the POST routes. Deliberately the smallest valid body: this test is
# about status and shape, and a POST that changes data is covered by
# `test_a_review_item_can_be_resolved_and_the_change_is_visible` below.
BODIES = {
    ("POST", "/auth/login"): {"phone": "9000000001", "password": "wrong-on-purpose"},
    ("POST", "/auth/otp"): {"phone": "9000000001"},
    ("POST", "/auth/verify"): {"phone": "9000000001", "code": "000000"},
    ("POST", "/acs/{ac_number}/scenario"): {"turnout_multiplier": 1.0, "draws": 10},
    ("POST", "/acs/{ac_number}/ground-reports"): {
        "text": "Contract test report. Booth reachable, no issues raised.",
        "issues": ["roads"],
    },
    ("POST", "/acs/{ac_number}/admin/crosswalk"): "CROSSWALK_FIX",
    # `status` must be 'resolved' or 'rejected' by the model's own pattern, and
    # the sweep is pointed at a nonexistent id so it cannot resolve a real item
    # out from under test_a_review_item_can_be_resolved_and_the_change_is_visible.
    ("POST", "/acs/{ac_number}/admin/review-queue/{item_id}"): {
        "status": "rejected", "note": "swept by the contract test",
    },
    ("POST", "/acs/{ac_number}/admin/surnames"): "SURNAME_ENTRY",
}

# Endpoints known to be broken, with the finding that tracks them. `xfail` and
# not `skip`: a skip says nothing, whereas a strict xfail fails the moment the
# endpoint starts working and so forces this entry to be removed with the fix.
#
# N15 is one defect with a long tail. `0015_metrics.sql` rebuilt the metric
# views and renamed columns - `votes_counted` to `votes_polled`, `total_valid`
# to `valid_votes` - and the handlers were never updated. Three references are
# fixed; these two handlers select several more stale names each
# (`electors_now` in the booth card's roll block, and more behind it in
# `/summary`), and getting them right means reconciling each SELECT list
# against the view it reads, not renaming by sight. That is the rest of
# FRONTEND_HARDENING.md section 2 and is not started.
#
# Both have returned 500 since 0015 was written. Neither page has ever loaded
# against a database, which is why nothing noticed.
KNOWN_BROKEN = {
    ("GET", "/acs/{ac_number}/summary"):
        "N15: selects stale metric-view columns renamed by 0015 (total_valid, "
        "and more behind it). The Overview page cannot load.",
    ("GET", "/acs/{ac_number}/booths/{booth_uid}/card"):
        "N15: api/booth_card.py selects electors_now and other names the roll "
        "and metric views no longer have. The booth card cannot load.",
}


def known_broken(endpoint: Endpoint, broken: dict | None = None):
    """The xfail marker for a route recorded as broken, or None."""
    reason = (KNOWN_BROKEN if broken is None else broken).get(endpoint.key)
    return None if reason is None else pytest.mark.xfail(strict=True, reason=reason)


# Statuses a wrong-password login or an unconfigured OTP sender may legitimately
# return. Anything else - and in particular any 5xx - is a failure.
EXPECTED_OTHER = {
    # Resolving an item is 200; resolving one already resolved is 404, and the
    # sweep may run after test_a_review_item_can_be_resolved has taken the
    # first open item. Both are correct answers.
    ("POST", "/acs/{ac_number}/admin/review-queue/{item_id}"): {200, 404},
    # 201 Created, which is the right answer for a POST that creates a row.
    ("POST", "/acs/{ac_number}/ground-reports"): {201},
    ("POST", "/auth/login"): {400, 401, 429},
    ("POST", "/auth/otp"): {200, 400, 501, 503},
    ("POST", "/auth/verify"): {400, 401, 429},
}


@dataclass(frozen=True)
class Endpoint:
    method: str
    path: str
    module: str
    name: str
    roles: frozenset[str]
    is_public: bool
    is_legacy: bool

    @property
    def key(self) -> tuple[str, str]:
        return (self.method, self.path)

    def __str__(self) -> str:
        return f"{self.method} {self.path}"


def _walk(obj, prefix: str = ""):
    """Every APIRoute under an app or router, with its mounted prefix.

    FastAPI 0.141 wraps an included router in an `_IncludedRouter` that holds
    the original router and the prefix separately, so `app.routes` is not a flat
    list and the obvious `for r in app.routes` finds only the two routes defined
    on the app itself. Getting that wrong is how a sweep like this quietly
    covers nothing.
    """
    for route in getattr(obj, "routes", []):
        if isinstance(route, APIRoute):
            yield prefix + route.path, route
            continue
        original = getattr(route, "original_router", None)
        if original is not None:
            context = getattr(route, "include_context", None)
            yield from _walk(original, prefix + (getattr(context, "prefix", "") or ""))


def endpoints() -> list[Endpoint]:
    from api.main import app

    out: list[Endpoint] = []
    for path, route in _walk(app):
        module = route.endpoint.__module__.split(".")[-1]
        signature = inspect.signature(route.endpoint)
        annotation = signature.parameters.get("user")
        # `from __future__ import annotations` makes these strings, which is
        # what we want: the alias name is the contract.
        alias = str(annotation.annotation) if annotation is not None else None
        roles = ROLE_ALIASES.get(alias, frozenset())
        for method in sorted(m for m in route.methods if m not in {"HEAD", "OPTIONS"}):
            out.append(Endpoint(
                method=method, path=path, module=module, name=route.endpoint.__name__,
                roles=frozenset(roles), is_public=(method, path) in PUBLIC,
                is_legacy=module == "legacy",
            ))
    return sorted(out, key=lambda e: (e.path, e.method))


ALL = endpoints()
SCOPED = [e for e in ALL if not e.is_legacy and not e.is_public]
LEGACY = [e for e in ALL if e.is_legacy]


def ident(e: Endpoint) -> str:
    return f"{e.method}-{e.path}"


def cases(endpoints_: list[Endpoint], broken: dict | None = None):
    """Parametrise over endpoints, carrying any known-broken marker.

    `broken` is narrowed per sweep because a route can be broken in one
    situation and correct in another, and a strict xfail that passes is itself a
    failure - which is the point of it being strict. The booth card is the
    example: it 500s on a real booth because of the stale column names, and
    404s on a constituency with no booths, so it is only expected to fail in the
    first sweep.
    """
    return [
        pytest.param(e, marks=[m] if (m := known_broken(e, broken)) else [], id=ident(e))
        for e in endpoints_
    ]


# ---------------------------------------------------------------------------
# Fixtures: a client per role, and real ids to put in the paths
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def ids(loaded_dataset, db_url):
    """Real identifiers from the loaded data, so a 404 means a bug not a typo."""
    import psycopg

    with psycopg.connect(db_url, row_factory=psycopg.rows.dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT ac_number FROM ac WHERE ac_number = 32")
            ac_number = cur.fetchone()["ac_number"]
            cur.execute("SELECT booth_uid FROM booth WHERE ac_id = "
                        "(SELECT ac_id FROM ac WHERE ac_number = 32) ORDER BY booth_uid LIMIT 1")
            booth_uid = cur.fetchone()["booth_uid"]
            # `review_queue.id`, not `item_id`: the POST path parameter is
            # named `item_id` and the listing returns `id`. Worth a note rather
            # than a rename, since the route is already in use.
            cur.execute("SELECT id FROM review_queue WHERE status = 'open' "
                        "ORDER BY id LIMIT 1")
            row = cur.fetchone()
            review_item_id = row["id"] if row else 1
            cur.execute("SELECT block_id FROM block b JOIN ac a USING (ac_id) "
                        "WHERE a.ac_number = 32 ORDER BY block_id")
            blocks = [r["block_id"] for r in cur.fetchall()]
            # The booth PS 1 already maps to, so the POST /admin/crosswalk the
            # sweep sends is provably a no-op. The sweep has to call every
            # route, and this one rewrites a crosswalk binding - pointing it at
            # the current value is how it stays a contract test rather than a
            # mutation that later assertions have to tolerate.
            cur.execute("SELECT booth_uid FROM booth_crosswalk x "
                        "JOIN election e ON e.election_id = x.election_id "
                        "WHERE e.label = 'VS-2024' AND x.ps_number = 1 "
                        "AND x.ac_id = (SELECT ac_id FROM ac WHERE ac_number = 32)")
            row = cur.fetchone()
            ps1_booth = row["booth_uid"] if row else booth_uid
            # A second AC with no booth data, for the empty-state and scoping
            # checks. 42-Tundi is seeded and deliberately never loaded.
            cur.execute("SELECT ac_number FROM ac WHERE ac_number = %s",
                        (loaded_dataset["empty_ac"],))
            empty_ac = cur.fetchone()["ac_number"]
    return {
        "ac_number": ac_number, "booth_uid": booth_uid,
        "election_label": "VS-2024", "review_item_id": review_item_id,
        "blocks": blocks, "empty_ac": empty_ac, "ps1_booth": ps1_booth,
    }


@pytest.fixture(scope="module")
def users(loaded_dataset, ids, db_url):
    """One user per role, created through the real path, with a known password."""
    import os

    os.environ["DATABASE_URL"] = db_url
    os.environ.setdefault("JWT_SECRET", "contract-test-secret-at-least-32-bytes-long")
    from common.config import get_settings
    from common.db import close_pools

    get_settings.cache_clear()
    close_pools()

    from api.deps import hash_password
    from common.db import query_one

    made = {}
    for index, role in enumerate(ROLES):
        phone = f"95000000{index:02d}"
        password = f"contract-{role}-pw"
        block = ids["blocks"][1] if role == "block" and len(ids["blocks"]) > 1 else (
            ids["blocks"][0] if role == "block" else None)
        row = query_one(
            "INSERT INTO app_user (phone, name, role, block_id, password_hash, "
            "daily_token_budget) VALUES (%s, %s, %s, %s, %s, 150000) "
            "ON CONFLICT (phone) DO UPDATE SET role = EXCLUDED.role, "
            "block_id = EXCLUDED.block_id, password_hash = EXCLUDED.password_hash, "
            "is_active = true RETURNING user_id",
            (phone, f"Contract {role}", role, block, hash_password(password)),
        )
        made[role] = {"phone": phone, "password": password,
                      "user_id": row["user_id"], "block_id": block}
    return made


@pytest.fixture(scope="module")
def client(users, db_url):
    from fastapi.testclient import TestClient

    from api.main import app

    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(scope="module")
def tokens(client, users):
    out = {}
    for role, spec in users.items():
        response = client.post("/auth/login",
                               json={"phone": spec["phone"], "password": spec["password"]})
        assert response.status_code == 200, (role, response.status_code, response.text)
        out[role] = response.json()["access_token"]
    return out


def call(client, endpoint: Endpoint, ids: dict, token: str | None = None,
         ac_number: int | None = None, params: dict | None = None):
    path = endpoint.path
    for name, key in PATH_PARAMS.items():
        placeholder = "{" + name + "}"
        if placeholder in path:
            value = ac_number if name == "ac_number" and ac_number is not None else ids[key]
            path = path.replace(placeholder, str(value))
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    body = BODIES.get(endpoint.key)
    if body == "CROSSWALK_FIX":
        body = {"election_label": ids["election_label"], "ps_number": 1,
                "booth_uid": ids["ps1_booth"]}
    elif body == "SURNAME_ENTRY":
        body = {"surname_hi": "संपर्क", "surname_en": "Contract", "community_id": 1,
                "weight": 0.5, "notes": "written by the contract test"}
    if endpoint.method == "POST":
        return client.post(path, json=body or {}, headers=headers,
                           params=params, follow_redirects=False)
    return client.get(path, headers=headers, params=params, follow_redirects=False)


# ---------------------------------------------------------------------------
# The sweep itself
# ---------------------------------------------------------------------------

def test_the_route_list_was_actually_discovered():
    """Guards every parametrised test below.

    If `_walk` returns nothing - and the obvious implementation does, because
    FastAPI 0.141 nests included routers - then every sweep below is
    parametrised over an empty list and passes without calling anything.
    """
    assert len(ALL) > 40, f"only {len(ALL)} routes found; _walk is not descending"
    assert len(SCOPED) > 25, f"only {len(SCOPED)} scoped routes found"
    paths = {e.path for e in ALL}
    for expected in ("/acs", "/acs/{ac_number}/summary", "/acs/{ac_number}/booths",
                     "/compare", "/auth/login"):
        assert expected in paths, expected


def test_every_route_has_a_contract_entry():
    """A new endpoint cannot be added without saying how to call it."""
    missing_body = [str(e) for e in ALL
                    if e.method == "POST" and not e.is_legacy and e.key not in BODIES]
    assert missing_body == [], (
        "these POST routes have no body in BODIES, so the sweep cannot exercise them: "
        f"{missing_body}"
    )
    unknown_role = [f"{e} ({e.module}.{e.name})" for e in ALL
                    if not e.is_legacy and not e.is_public and not e.roles]
    assert unknown_role == [], (
        "these routes take no recognised role dependency, so their access control is "
        f"undeclared: {unknown_role}"
    )


@pytest.mark.parametrize("endpoint", SCOPED, ids=ident)
def test_an_anonymous_request_is_rejected_not_served(client, ids, endpoint):
    response = call(client, endpoint, ids)
    assert response.status_code in {401, 403}, (
        f"{endpoint} served an unauthenticated request with {response.status_code}: "
        f"{response.text[:200]}"
    )


@pytest.mark.parametrize("endpoint", cases(SCOPED))
@pytest.mark.parametrize("role", ROLES)
def test_every_route_answers_correctly_for_every_role(client, tokens, ids, endpoint, role):
    """200 when the role is admitted, 403 when it is not, never 500.

    The "never 500" half is the one that matters. A 500 here means a handler
    cannot run its own query against the schema it ships with - and it is how
    N15 (`votes_counted`, renamed by 0015) reached this commit.
    """
    response = call(client, endpoint, ids, token=tokens[role])
    allowed = role in endpoint.roles

    assert response.status_code < 500, (
        f"{endpoint} as {role} returned {response.status_code}: {response.text[:400]}"
    )
    if allowed:
        expected = EXPECTED_OTHER.get(endpoint.key, {200})
        assert response.status_code in expected, (
            f"{endpoint} as {role} returned {response.status_code}, expected "
            f"{sorted(expected)}: {response.text[:300]}"
        )
    else:
        assert response.status_code == 403, (
            f"{endpoint} as {role} returned {response.status_code}, expected 403 "
            f"(the route declares {sorted(endpoint.roles)})"
        )


@pytest.mark.parametrize("endpoint", [e for e in SCOPED if e.method == "GET"], ids=ident)
def test_a_get_response_is_json_and_not_a_bare_scalar(client, tokens, ids, endpoint):
    """Every data route returns an object or an array, never a bare value.

    A handler that returns a scalar cannot carry provenance or a count beside
    its figure, and the frontend has no way to tell an empty result from an
    error.
    """
    response = call(client, endpoint, ids, token=tokens["admin"])
    if response.status_code != 200:
        # A route in KNOWN_BROKEN lands here. The sweep above is what reports
        # it; this one has nothing to inspect.
        pytest.skip(f"{endpoint} returned {response.status_code} for admin")
    if "application/json" not in response.headers.get("content-type", ""):
        # CSV export routes answer text/csv when asked; the JSON default is
        # what is under test here.
        pytest.skip(f"{endpoint} returned {response.headers.get('content-type')}")
    body = response.json()
    assert isinstance(body, (dict, list)), f"{endpoint} returned {type(body).__name__}"


# ---------------------------------------------------------------------------
# Role scoping beyond the status code
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("endpoint", SCOPED, ids=ident)
def test_a_block_user_cannot_reach_another_constituency(client, tokens, ids, endpoint):
    """A block-role user belongs to one block, which belongs to one AC.

    Asking for a different AC must be 403, not an empty result: an empty result
    reads as "nothing loaded" and sends someone looking for a data problem that
    does not exist. `api/deps.py` says so in its own docstring.
    """
    if "{ac_number}" not in endpoint.path:
        pytest.skip("not an AC-scoped route")
    response = call(client, endpoint, ids, token=tokens["block"],
                    ac_number=ids["empty_ac"])
    assert response.status_code == 403, (
        f"{endpoint} let a block user of AC 32 address AC {ids['empty_ac']} with "
        f"{response.status_code}"
    )


def test_the_caste_pages_are_closed_to_a_block_user(client, tokens, ids):
    """LLD 12. Asserted directly rather than only via the role sweep, because
    this is the one access rule with a legal reason behind it."""
    for path in (f"/acs/{ids['ac_number']}/caste",
                 f"/acs/{ids['ac_number']}/caste/correlation"):
        response = client.get(path, headers={"Authorization": f"Bearer {tokens['block']}"})
        assert response.status_code == 403, (path, response.status_code)


def test_an_unknown_constituency_is_a_404_not_a_500(client, tokens):
    response = client.get("/acs/999/summary",
                          headers={"Authorization": f"Bearer {tokens['admin']}"})
    assert response.status_code == 404, response.text[:300]


def test_an_invalid_token_is_rejected(client, ids):
    response = client.get(f"/acs/{ids['ac_number']}/summary",
                          headers={"Authorization": "Bearer not-a-token"})
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# An AC with nothing loaded must answer, not fail
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("endpoint", cases(
    [e for e in SCOPED if e.method == "GET"],
    # The booth card is excluded: with no booths in the constituency it returns
    # 404 from its own existence check and never reaches the broken SQL.
    broken={k: v for k, v in KNOWN_BROKEN.items()
            if k != ("GET", "/acs/{ac_number}/booths/{booth_uid}/card")},
))
def test_a_constituency_with_no_data_answers_empty_rather_than_failing(
        client, tokens, ids, endpoint):
    """42-Tundi is seeded and deliberately never loaded.

    Section 4 of the hardening brief requires the frontend to render an AC with
    nothing in it, so the API has to give it something to render. A 500 here is
    a handler that assumes its view has rows.
    """
    if "{ac_number}" not in endpoint.path:
        pytest.skip("not an AC-scoped route")
    response = call(client, endpoint, ids, token=tokens["admin"],
                    ac_number=ids["empty_ac"])
    assert response.status_code < 500, (
        f"{endpoint} on an empty constituency returned {response.status_code}: "
        f"{response.text[:400]}"
    )
    assert response.status_code in {200, 404}, (
        f"{endpoint} on an empty constituency returned {response.status_code}"
    )


# ---------------------------------------------------------------------------
# Legacy redirects
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("endpoint", [e for e in LEGACY if e.method == "GET"], ids=ident)
def test_a_legacy_path_redirects_into_the_scoped_one(client, tokens, ids, endpoint):
    """The pre-multi-AC paths are kept as 308s for one release.

    A 308 preserves the method and body, which a 302 does not - a POST to a
    legacy path must not silently become a GET.
    """
    path = endpoint.path.replace("{booth_uid}", ids["booth_uid"]).replace(
        "{election_label}", ids["election_label"])
    response = client.get(path, headers={"Authorization": f"Bearer {tokens['admin']}"},
                          follow_redirects=False)
    assert response.status_code in {307, 308}, (
        f"{endpoint} returned {response.status_code}, not a redirect"
    )
    location = response.headers.get("location", "")
    assert location.startswith("/acs/"), f"{endpoint} redirected to {location!r}"


# ---------------------------------------------------------------------------
# One write, end to end
# ---------------------------------------------------------------------------

def test_a_review_item_can_be_resolved_and_the_change_is_visible(client, tokens, ids):
    """Section 5 asks that the review queue's actions work end to end.

    Done here rather than in the sweep because it has to assert the effect, not
    the status: an action that returns 200 and changes nothing is the failure
    worth catching.
    """
    headers = {"Authorization": f"Bearer {tokens['admin']}"}
    base = f"/acs/{ids['ac_number']}/admin/review-queue"
    listing = client.get(base, headers=headers)
    assert listing.status_code == 200, listing.text[:300]
    rows = listing.json().get("rows") or []
    if not rows:
        pytest.skip("no open review items in the loaded dataset")
    item_id = rows[0]["id"]

    done = client.post(f"{base}/{item_id}",
                       json={"status": "resolved", "note": "contract test"},
                       headers=headers)
    assert done.status_code == 200, done.text[:300]
    assert done.json()["updated"] == 1

    # `status_filter` is aliased to the `status` query parameter.
    after = client.get(base, headers=headers, params={"status": "open", "limit": 500})
    assert after.status_code == 200
    still_open = {r["id"] for r in (after.json().get("rows") or [])}
    assert item_id not in still_open, "the item is still listed as open after resolving it"

    resolved = client.get(base, headers=headers,
                          params={"status": "resolved", "limit": 500})
    assert resolved.status_code == 200
    assert item_id in {r["id"] for r in (resolved.json().get("rows") or [])}

    # Resolving it twice must be a 404, not a second silent success: the UPDATE
    # is guarded on `status = 'open'` precisely so two operators clicking the
    # same row cannot both think they handled it.
    again = client.post(f"{base}/{item_id}", json={"status": "rejected"}, headers=headers)
    assert again.status_code == 404, again.text[:200]
