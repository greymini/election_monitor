"""The API must run and serve every page from the database with no worker
service deployed.

That is a deployment requirement, so it is pinned as a test rather than left as
a property somebody checks by hand. Four things have to hold:

  1. No API module imports `worker`, `ingest` or `news` at module scope. If one
     did, the API image - which contains only common/, api/, chatbot/ and
     analytics/ - would fail to start, and so would `uvicorn api.main:app` on a
     laptop with only requirements-api.txt installed.
  2. `api.main` imports with every worker-only dependency absent. This test
     process is itself the evidence: sentence-transformers, apscheduler,
     feedparser, pytesseract, boto3, pandas, scipy and numpy are all missing
     from requirements-dev.txt's closure.
  3. Every endpoint the frontend calls exists in the route table. A page whose
     endpoint is missing does not serve from the database, it 404s.
  4. Every page's data comes from the API. A page rendering a hardcoded constant
     is not serving from the database either, whatever the route table says.

What these tests cannot do is prove the *responses* are right, because that
needs a populated Postgres. The route table and the import graph are static
facts; the data is not. See UAT_READINESS.md for the operator commands that
close that gap.

One thing deliberately not asserted: that the materialized views hold rows. With
no worker, nothing refreshes them on a schedule, so `python -m analytics.refresh`
becomes an operator step rather than a cron job. That is a runbook fact, not a
code defect, and it is recorded in RUN.md.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WEB_SRC = ROOT / "web" / "src"

# Packages that live only in the worker image. requirements-api.txt has none of
# them, and docker/Dockerfile.api copies only common, api, chatbot, analytics.
WORKER_ONLY_PACKAGES = ("worker", "ingest", "news")

# Third-party distributions in requirements-worker.txt but not requirements-api.txt.
WORKER_ONLY_DEPENDENCIES = (
    "sentence_transformers",
    "apscheduler",
    "feedparser",
    "pytesseract",
    "boto3",
    "pandas",
    "scipy",
    "numpy",
    "pdfplumber",
)

# Routes that legitimately serve without touching the database.
NON_DB_ROUTES = {
    "/health",          # a DB probe, so it must answer when the DB is down
    "/config",          # boot-time feature flags, read from the environment
    "/openapi.json",
    "/docs",
    "/docs/oauth2-redirect",
    "/redoc",
}


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def app_routes() -> list[tuple[frozenset[str], str]]:
    """Every (methods, path) the app serves.

    FastAPI 0.141 stopped flattening `include_router` into `app.routes`: it
    appends one `_IncludedRouter` wrapper per call and keeps the real routes on
    `original_router`. Walking both shapes means this test survives a FastAPI
    version change in either direction rather than silently finding five routes
    and passing.
    """
    from api.main import app

    found: list[tuple[frozenset[str], str]] = []

    def walk(routes) -> None:
        for route in routes:
            nested = getattr(route, "original_router", None)
            if nested is not None:
                walk(nested.routes)
                continue
            if getattr(route, "routes", None) and not hasattr(route, "methods"):
                walk(route.routes)
                continue
            path = getattr(route, "path", None)
            if path:
                found.append((frozenset(getattr(route, "methods", []) or []), path))

    walk(app.routes)
    return found


def normalise(path: str) -> str:
    """Compare paths ignoring parameter names and query strings.

    `/results/{election_label}/booths` and `` `/results/${chosen}/booths` ``
    are the same endpoint; `/caste?min_conf=0.4` is the `/caste` endpoint.
    """
    path = path.split("?", 1)[0]
    path = re.sub(r"\$\{[^}]*\}", "{}", path)   # JS template substitution
    # A nested template literal - `/local-results${seatType ? `?seat_type=...` : ''}`
    # - leaves a dangling '${' because the inner backtick ended the outer match.
    # Everything from there on is a conditional query string, which is not part
    # of the endpoint, so truncate rather than try to parse JS.
    path = path.split("${", 1)[0].split("$", 1)[0]
    path = re.sub(r"\{[^}]*\}", "{}", path)     # FastAPI path parameter
    return path.rstrip("/") or "/"


def frontend_endpoints() -> dict[str, set[str]]:
    """Every API path the frontend calls, mapped to the files that call it.

    Scans for the four ways a request leaves the app: `api.get`, `api.post`,
    `downloadCsv` and the one raw `fetch` in ChatPanel. Paths are string or
    template literals beginning with a slash.
    """
    calls: dict[str, set[str]] = {}
    # Two shapes: a bare literal, and ac.path('/x') which prefixes
    # /acs/{ac_number}. The second is how every data call is written now, so a
    # scanner that only understood the first would find almost nothing and this
    # test would pass vacuously.
    pattern = re.compile(
        r"(?:api\.(?:get|post)\s*(?:<[^>]*>)?\s*\(|downloadCsv\s*\(|fetch\s*\()\s*"
        r"(ac\.path\s*\(\s*)?[`'\"]([^`'\"]*)[`'\"]",
        re.DOTALL,
    )
    for source in sorted(WEB_SRC.rglob("*.ts*")):
        if source.name == "api.ts":
            continue  # the client itself; its own paths are tested via callers
        text = source.read_text(encoding="utf-8")
        for match in pattern.finditer(text):
            scoped = match.group(1) is not None
            raw = match.group(2)
            # ChatPanel interpolates the base URL into the literal.
            raw = re.sub(r"^\$\{[^}]*\}", "", raw)
            if not raw.startswith("/"):
                continue
            if scoped:
                raw = "/acs/{ac_number}" + raw
            calls.setdefault(normalise(raw), set()).add(
                source.relative_to(ROOT).as_posix()
            )
    return calls


def module_scope_imports(path: Path) -> set[str]:
    """Top-level imported root packages. Imports inside a function or an
    `if TYPE_CHECKING:` block are not module scope and do not affect startup."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            roots |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def api_source_files() -> list[Path]:
    return sorted(p for p in (ROOT / "api").rglob("*.py") if p.name != "__init__.py")


# --------------------------------------------------------------------------
# 1. No worker coupling
# --------------------------------------------------------------------------


@pytest.mark.parametrize("source", api_source_files(), ids=lambda p: p.name)
def test_api_does_not_import_worker_packages_at_module_scope(source):
    offenders = module_scope_imports(source) & set(WORKER_ONLY_PACKAGES)
    assert not offenders, (
        f"{source.relative_to(ROOT).as_posix()} imports {sorted(offenders)} at module scope. "
        "The API image does not contain those packages, so the app would not start. "
        "Move the import inside the handler."
    )


def test_the_one_permitted_news_import_is_function_local():
    """`/news?q=` wants an embedding for vector ranking and cannot have one in
    the API image. That is fine as long as the import is inside the handler and
    guarded, so the route degrades to date ordering instead of 500ing."""
    source = ROOT / "api" / "routers" / "news.py"
    text = source.read_text(encoding="utf-8")
    assert "from news.embed import embed_query" in text
    assert "news" not in module_scope_imports(source)
    # and the guard is present
    tree = ast.parse(text, filename=str(source))
    guarded = any(
        isinstance(node, ast.Try)
        and any(
            isinstance(child, (ast.Import, ast.ImportFrom))
            and "news" in ast.dump(child)
            for child in ast.walk(node)
        )
        for node in ast.walk(tree)
    )
    assert guarded, "the news.embed import must sit in a try/except so the route degrades"


def test_api_never_touches_the_filesystem_or_spawns_a_process():
    """The API serves from the database. It does not read a source PDF, write a
    cache or shell out - those belong to ingestion, which may not even be
    installed alongside it."""
    banned = {"subprocess", "shutil", "tempfile"}
    for source in api_source_files():
        offenders = module_scope_imports(source) & banned
        assert not offenders, f"{source.name} imports {sorted(offenders)}"
        text = source.read_text(encoding="utf-8")
        assert "open(" not in text.replace("urlopen(", ""), f"{source.name} opens a file"


# --------------------------------------------------------------------------
# 2. Imports without worker dependencies
# --------------------------------------------------------------------------


class _BlockImports:
    """A meta-path finder that makes the named packages unimportable.

    Relying on "these are not installed in the dev environment" is fragile: the
    moment one of them is needed by another test - pdfplumber is, for the PDF
    fixtures - the check quietly stops proving anything. Blocking the import
    outright reproduces the API image's dependency set regardless of what the
    dev environment happens to contain.
    """

    def __init__(self, blocked: tuple[str, ...]) -> None:
        self.blocked = blocked

    def find_spec(self, fullname, path=None, target=None):
        root = fullname.split(".")[0]
        if root in self.blocked:
            raise ImportError(f"{fullname} is blocked: not present in the API image")
        return None


def test_api_imports_with_worker_dependencies_blocked():
    """The claim, exercised directly. Every worker-only distribution is made
    unimportable, every api/chatbot/analytics module is dropped from the module
    cache, and the app is imported from scratch. If any module-scope import
    reached into the worker's dependency set, this raises ImportError."""
    import importlib
    import sys

    blocked = WORKER_ONLY_PACKAGES + WORKER_ONLY_DEPENDENCIES
    finder = _BlockImports(blocked)
    saved = {
        name: module for name, module in sys.modules.items()
        if name.split(".")[0] in ("api", "chatbot", "analytics", "common") + blocked
    }
    for name in saved:
        del sys.modules[name]
    sys.meta_path.insert(0, finder)
    try:
        main = importlib.import_module("api.main")
        assert main.app.title
        routes = [
            getattr(r, "path", None)
            for r in main.app.routes
        ]
        assert routes, "no routes at all"
    finally:
        sys.meta_path.remove(finder)
        for name in [n for n in sys.modules if n.split(".")[0] in ("api", "chatbot", "analytics")]:
            del sys.modules[name]
        sys.modules.update(saved)


def test_the_route_table_is_not_almost_empty():
    """Keeps every route test below honest. FastAPI 0.141 stopped flattening
    included routers into app.routes; a walker that does not know that finds
    five routes and every coverage assertion passes vacuously."""
    assert len(app_routes()) > 20, (
        f"found only {len(app_routes())} routes - app_routes() probably needs "
        "updating for a FastAPI change"
    )


def test_scenario_router_imports_analytics_but_analytics_is_dependency_free():
    """`api/routers/scenario.py` imports `analytics.scenario` at module scope,
    which is only safe because that module is pure Python. If it grew a numpy
    import the API would stop starting, and this is where that shows up."""
    source = ROOT / "analytics" / "scenario.py"
    third_party = module_scope_imports(source) - {
        "__future__", "random", "statistics", "dataclasses", "typing", "math",
        "collections", "itertools", "functools", "common", "analytics", "enum", "datetime",
    }
    assert not third_party, (
        f"analytics/scenario.py now imports {sorted(third_party)} at module scope, and "
        "api/routers/scenario.py imports it at module scope, so the API image "
        "would need those too."
    )


# --------------------------------------------------------------------------
# 3. Route coverage for every page
# --------------------------------------------------------------------------


# /chat only exists when CHAT_ENABLED=true, which is not the default. The
# frontend must therefore gate the panel on /config rather than assume the route
# is there - asserted separately below.
CONDITIONAL_ENDPOINTS = {"/chat"}


def test_every_frontend_endpoint_exists_in_the_route_table():
    served = {normalise(path) for _, path in app_routes()}
    missing = {
        endpoint: sorted(callers)
        for endpoint, callers in frontend_endpoints().items()
        if endpoint not in served and endpoint not in CONDITIONAL_ENDPOINTS
    }
    assert not missing, (
        "the frontend calls endpoints the API does not serve:\n"
        + "\n".join(f"  {ep}  <- {', '.join(files)}" for ep, files in sorted(missing.items()))
    )


def test_chat_routes_are_absent_by_default_and_present_when_enabled():
    """A5. With the feature parked the routes must not merely be hidden in the
    UI - they must not exist, so `sqlglot` and `anthropic` need not be installed
    at all."""
    import importlib
    import sys

    from common.config import get_settings

    assert "/chat" not in {normalise(p) for _, p in app_routes()}, (
        "chat routes are mounted with CHAT_ENABLED unset; the default must be off"
    )

    saved = {n: m for n, m in sys.modules.items() if n.split(".")[0] in ("api", "common")}
    for name in saved:
        del sys.modules[name]
    import os

    previous = os.environ.get("CHAT_ENABLED")
    os.environ["CHAT_ENABLED"] = "true"
    try:
        from common.config import get_settings as fresh_settings

        fresh_settings.cache_clear()
        main = importlib.import_module("api.main")
        paths = set()

        def walk(routes):
            for route in routes:
                nested = getattr(route, "original_router", None)
                if nested is not None:
                    walk(nested.routes)
                    continue
                if getattr(route, "path", None):
                    paths.add(normalise(route.path))

        walk(main.app.routes)
        assert "/chat" in paths, "CHAT_ENABLED=true did not mount the chat routes"
    finally:
        if previous is None:
            os.environ.pop("CHAT_ENABLED", None)
        else:
            os.environ["CHAT_ENABLED"] = previous
        for name in [n for n in sys.modules if n.split(".")[0] in ("api", "common")]:
            del sys.modules[name]
        sys.modules.update(saved)
        get_settings.cache_clear()


def test_the_frontend_gates_the_chat_panel_on_the_config_flag():
    """Since /chat may not exist, the panel must not be reachable unless the
    server says the feature is on."""
    layout = (WEB_SRC / "components" / "Layout.tsx").read_text(encoding="utf-8")
    assert "config?.chat_enabled" in layout, (
        "Layout must read chat_enabled from /config before rendering ChatPanel"
    )
    app_tsx = (WEB_SRC / "App.tsx").read_text(encoding="utf-8")
    assert "getConfig" in app_tsx, "App must fetch /config at boot"


def test_the_endpoint_scan_found_the_calls_it_should():
    """Keeps the coverage test honest: a regex that matches nothing would make
    it pass trivially."""
    endpoints = frontend_endpoints()
    for expected in ("/acs/{}/summary", "/acs/{}/booths", "/acs/{}/areas",
                     "/acs/{}/caste", "/acs/{}/transfer", "/acs/{}/news",
                     "/acs/{}/rolls/changes", "/acs/{}/scenario",
                     "/acs/{}/admin/review-queue", "/acs/{}/results/{}/booths",
                     "/acs", "/compare"):
        assert expected in endpoints, f"the scan missed {expected}"
    assert len(endpoints) >= 15


def test_csv_export_paths_are_the_same_endpoints():
    """`downloadCsv` appends `format=csv` to a path the page already fetches, so
    every export target must be a real route too - covered by the test above,
    asserted here so the intent is explicit."""
    served = {normalise(path) for _, path in app_routes()}
    for path in ("/acs/{}/results/{}/booths", "/acs/{}/rolls/changes", "/acs/{}/transfer"):
        assert path in served


# --------------------------------------------------------------------------
# 4. Every page's data comes from the API
# --------------------------------------------------------------------------


def page_files() -> list[Path]:
    return sorted((WEB_SRC / "pages").glob("*.tsx"))


@pytest.mark.parametrize("page", page_files(), ids=lambda p: p.stem)
def test_every_page_fetches_its_data_from_the_api(page):
    """A page that renders a hardcoded constant is not serving from the
    database, however healthy the route table looks.

    Login is exempt: it posts credentials and renders nothing from the DB.
    """
    if page.stem == "Login":
        pytest.skip("Login posts credentials; it renders no data")

    text = page.read_text(encoding="utf-8")
    calls = re.findall(r"api\.(?:get|post)\s*(?:<[^>]*>)?\s*\(", text)
    assert calls, (
        f"{page.name} makes no API call, so whatever it displays is hardcoded "
        "and cannot be the database's answer"
    )


def test_no_page_hardcodes_election_results():
    """Vote counts and margins in the frontend source are the specific failure
    this guards: they look authoritative, they are never checked against the
    loaded data, and they go stale silently.

    Numbers in i18n files, CSS and test fixtures are fine; these are TSX
    literals shaped like Indian-grouped vote counts.
    """
    offenders: list[str] = []
    # e.g. 94,042 / 1,33,499 - at least two comma groups, so years and small
    # integers do not trip it.
    vote_like = re.compile(r"\b\d{1,2},\d{2,3}(?:,\d{3})*\b")
    for source in sorted(WEB_SRC.rglob("*.tsx")):
        for lineno, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
            if vote_like.search(line) and "t(" not in line:
                offenders.append(f"{source.relative_to(ROOT).as_posix()}:{lineno}: {line.strip()}")
    assert not offenders, (
        "hardcoded vote counts in the frontend:\n  " + "\n  ".join(offenders)
    )


# --------------------------------------------------------------------------
# Auth coverage - a data route without auth would serve the DB to anyone
# --------------------------------------------------------------------------


def test_every_data_route_requires_authentication():
    """Only the documented public routes may be reachable without a token.

    The legacy paths are exempt because they are 308 redirects: they return a
    Location header, never a row, and answering an unauthenticated client with
    the new URL is more useful than a 401. `test_legacy_routes_only_redirect`
    checks that redirecting is all they do.
    """
    public = {"/health", "/config", "/auth/login", "/auth/otp", "/auth/verify",
              "/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}
    from api.routers.legacy import router as legacy_router

    public |= {normalise(getattr(r, "path", "")) for r in legacy_router.routes}

    from api.main import app

    unauthenticated: list[str] = []

    def walk(routes) -> None:
        for route in routes:
            nested = getattr(route, "original_router", None)
            if nested is not None:
                walk(nested.routes)
                continue
            path = getattr(route, "path", "")
            if not path or normalise(path) in public:
                continue
            dependant = getattr(route, "dependant", None)
            if dependant is None:
                continue
            flat = repr(getattr(route, "dependencies", [])) + repr(
                [d.call for d in dependant.dependencies]
            )
            if "current_user" not in flat and "require_role" not in flat and "checker" not in flat:
                unauthenticated.append(path)

    walk(app.routes)
    assert not unauthenticated, f"routes reachable without a token: {unauthenticated}"


# --------------------------------------------------------------------------
# Multi-AC scoping
# --------------------------------------------------------------------------


def test_every_data_route_is_scoped_to_a_constituency():
    """The spec's warning about a half-scoped schema applies to the API too: a
    data route outside /acs/{ac_number} either serves one hardcoded
    constituency or silently mixes all of them."""
    unscoped_ok = {
        "/health", "/config", "/acs", "/compare", "/acs/{}",
        "/auth/login", "/auth/otp", "/auth/verify", "/auth/me",
        "/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc",
    }
    from api.routers.legacy import router as legacy_router

    unscoped_ok |= {normalise(getattr(r, "path", "")) for r in legacy_router.routes}

    offenders = [
        path for _, path in app_routes()
        if normalise(path) not in unscoped_ok and not path.startswith("/acs/{ac_number}")
    ]
    assert not offenders, f"data routes outside the AC prefix: {sorted(set(offenders))}"


def test_every_scoped_route_actually_takes_the_ac_dependency():
    """Sitting under the prefix is not enough. A handler that ignores CurrentAC
    would serve every constituency's rows under one AC's URL - exactly the
    silent mixing the spec warns about."""
    import inspect

    from api.routers import admin, data, news, scenario

    missing: list[str] = []
    for module in (data, news, scenario, admin):
        for route in module.router.routes:
            handler = getattr(route, "endpoint", None)
            if handler is None:
                continue
            annotations = {
                str(param.annotation)
                for param in inspect.signature(handler).parameters.values()
            }
            if not any("CurrentAC" in a or "AC]" in a for a in annotations):
                missing.append(f"{module.__name__}.{handler.__name__}")
    assert not missing, f"scoped handlers that ignore the AC: {missing}"


def test_the_legacy_routes_cover_the_paths_that_moved():
    """Bookmarks, RUN.md and the LLD's API table all point at the old paths."""
    from api.routers.legacy import MOVED

    for path in ("/summary", "/booths", "/caste", "/transfer", "/scenario", "/areas"):
        assert path in MOVED, f"{path} moved without a redirect"


def test_legacy_routes_only_redirect():
    """They must not read anything. A legacy route that grew a query would be an
    unauthenticated data route, since they carry no auth dependency."""
    import inspect

    from api.routers import legacy

    source = inspect.getsource(legacy)
    for forbidden in ("query(", "query_one(", "execute("):
        assert forbidden not in source, f"legacy router calls {forbidden}"


def test_legacy_redirects_preserve_the_method():
    """308, not 301 or 302. POST /scenario and POST /ground-reports both moved,
    and 301/302 let a client turn them into GETs - which looks like a silently
    ignored request rather than an error."""
    import inspect

    from api.routers import legacy

    source = inspect.getsource(legacy)
    assert "HTTP_308_PERMANENT_REDIRECT" in source


def test_the_frontend_never_builds_an_unscoped_data_path():
    """Pages must go through ac.path(). A bare literal would hit the legacy
    redirect and show AC-32's numbers under whatever constituency is selected -
    the worst failure available here, because it looks like data."""
    offenders: list[str] = []
    allowed = {"/acs", "/compare", "/auth/login", "/auth/me", "/config", "/chat"}
    pattern = re.compile(
        r"(?:api\.(?:get|post)\s*(?:<[^>]*>)?\s*\(|downloadCsv\s*\()\s*"
        r"(ac\.path\s*\(\s*)?[`'\"]([^`'\"]*)[`'\"]"
    )
    for source in sorted(WEB_SRC.rglob("*.tsx")):
        for lineno, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
            for match in pattern.finditer(line):
                if match.group(1) is not None:
                    continue
                path = match.group(2)
                if path.startswith("/") and normalise(path) not in allowed:
                    offenders.append(
                        f"{source.relative_to(ROOT).as_posix()}:{lineno}: {path}"
                    )
    assert not offenders, "unscoped data paths in the frontend:\n  " + "\n  ".join(offenders)


def test_the_switcher_shows_the_unverified_badge():
    """Five of the six constituencies are seeded from secondary sources. Anyone
    reading a margin has to be able to tell which kind of number it is."""
    switcher = (WEB_SRC / "components" / "AcSwitcher.tsx").read_text(encoding="utf-8")
    assert "verified" in switcher
    assert "ac.unverified" in switcher


def test_the_selected_ac_is_in_the_url():
    """So a view can be shared - the spec asks for it, and "look at this booth"
    is the most common thing anyone sends a colleague."""
    helper = (WEB_SRC / "lib" / "ac.ts").read_text(encoding="utf-8")
    assert "useSearchParams" in helper
    assert "'ac'" in helper


def test_the_compare_page_renders_absence_as_a_dash():
    """A constituency with no Form 20 must not appear to have won by nothing."""
    compare = (WEB_SRC / "pages" / "Compare.tsx").read_text(encoding="utf-8")
    assert "Not loaded" in compare or "notLoaded" in compare
    assert "\u2014" in compare or "—" in compare
