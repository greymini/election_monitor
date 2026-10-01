"""Generate docs/design/ENDPOINTS.md: every API route against every frontend call site.

    python scripts/endpoint_inventory.py            # write docs/design/ENDPOINTS.md
    python scripts/endpoint_inventory.py --check    # fail if it is out of date

A generator rather than a hand-written table, because a hand-written one is
wrong within a week and nobody can tell. `--check` in the commit gate means the
document cannot drift from the code without the gate noticing.

What it flags:

  * a frontend call to a route that does not exist;
  * a route with no frontend caller;
  * a route whose response model is a bare `dict`, which is the state
    `FRONTEND_HARDENING.md` 2 asks to end - a bare dict generates no schema, so
    `openapi-typescript` produces `unknown` and the frontend has no contract to
    check against;
  * a query parameter the frontend sends that the route does not accept.

What it cannot do. Frontend paths are built at runtime - `ac.path('/caste?min_conf=' + x)`
is a template literal, not a constant - so the scanner normalises interpolations
to a parameter placeholder and matches the shape. It will miss a path assembled
in pieces across statements. The route-by-role contract test in
`tests/e2e/test_api_contract.py` is the real guarantee; this is the map.
"""

from __future__ import annotations

import argparse
import inspect
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]          # backend/
REPO_ROOT = APP_ROOT.parent
# Runnable as `python scripts/endpoint_inventory.py` from the application root,
# which does not put that root on the path the way `python -m` does.
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

WEB_SRC = REPO_ROOT / "frontend" / "src"
OUTPUT = REPO_ROOT / "docs" / "design" / "ENDPOINTS.md"

ROLE_ALIASES = {
    "AdminUser": "admin",
    "StrategistUser": "admin, strategist",
    "CurrentUser": "any signed-in",
}

# Parameters that are plumbing, not part of the contract.
HIDDEN_PARAMS = {"user", "ac", "request", "response", "body", "_path"}


@dataclass
class Route:
    method: str
    path: str
    module: str
    handler: str
    roles: str
    params: list[str] = field(default_factory=list)
    response_model: str = "dict (untyped)"
    is_legacy: bool = False

    @property
    def key(self) -> str:
        return f"{self.method} {self.path}"


@dataclass
class CallSite:
    file: str
    line: int
    method: str
    path: str
    query: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# The API side
# ---------------------------------------------------------------------------

def collect_routes() -> list[Route]:
    from fastapi.routing import APIRoute

    from api.main import app

    def walk(obj, prefix: str = ""):
        for route in getattr(obj, "routes", []):
            if isinstance(route, APIRoute):
                yield prefix + route.path, route
                continue
            original = getattr(route, "original_router", None)
            if original is not None:
                context = getattr(route, "include_context", None)
                yield from walk(original, prefix + (getattr(context, "prefix", "") or ""))

    routes: list[Route] = []
    for path, route in walk(app):
        signature = inspect.signature(route.endpoint)
        roles = "public"
        params: list[str] = []
        for name, parameter in signature.parameters.items():
            annotation = str(parameter.annotation)
            if name == "user":
                roles = ROLE_ALIASES.get(annotation, f"? ({annotation})")
                continue
            if name in HIDDEN_PARAMS:
                continue
            if "{" + name + "}" in path:
                continue
            optional = parameter.default is not inspect.Parameter.empty
            params.append(f"`{name}`{'' if not optional else '?'}")

        model = route.response_model
        model_name = getattr(model, "__name__", None)
        if model_name is None:
            model_name = "dict (untyped)" if model in (None, dict) else str(model)

        for method in sorted(m for m in route.methods if m not in {"HEAD", "OPTIONS"}):
            routes.append(Route(
                method=method, path=path,
                module=route.endpoint.__module__.split(".")[-1],
                handler=route.endpoint.__name__, roles=roles, params=params,
                response_model=model_name,
                is_legacy=route.endpoint.__module__.endswith("legacy"),
            ))
    return sorted(routes, key=lambda r: (r.is_legacy, r.path, r.method))


# ---------------------------------------------------------------------------
# The frontend side
# ---------------------------------------------------------------------------

# api.get('/x'), api.post(ac.path(`/y?z=1`)), downloadCsv(ac.path('/z'), ...)
CALL_RE = re.compile(
    r"\b(?:api\.(?P<verb>get|post)|(?P<csv>downloadCsv))\s*(?:<[^>]*>)?\s*\(\s*"
    r"(?P<arg>[^\n]+)")
# ac.path('/booths') or ac.path(`/booths/${uid}/card`) or '/acs'
SCOPED_RE = re.compile(r"ac\.path\(")
PATH_RE = re.compile(r"""(?P<quote>['"`])(?P<path>[^'"`]*)\1""")
# Innermost ${...}: no braces of its own, so repeated application collapses
# nesting from the inside out.
INTERP_RE = re.compile(r"\$\{[^{}]*\}")


# A placeholder with no braces of its own, so collapsing an inner `${...}` does
# not make the interpolation that contains it unmatchable.
SENTINEL = "@@INTERP@@"


def collapse_interpolations(text: str) -> str:
    """Replace every `${...}` with `{}`, innermost first.

    Nested template literals are why this is a loop rather than one `sub`.
    `ac.path(`/local-results${seatType ? `?seat_type=${seatType}` : ''}`)` has a
    backtick *inside* an interpolation, so a single pattern that stops at the
    next quote character truncates the path to `/local-results${seatType ` and
    the call looks like it points at a route that does not exist. A previous
    scanner in this repository reported exactly that and the finding was
    recorded as "two apparent misses were bugs in my scanner, not in the app".
    """
    previous = None
    while previous != text:
        previous = text
        text = INTERP_RE.sub(SENTINEL, text)
    return text.replace(SENTINEL, "{}")


def normalise(path: str, scoped: bool) -> tuple[str, list[str]]:
    """A frontend path to the route template it should match, plus its query keys.

    `${...}` becomes `{}`, matched positionally against the route's own `{name}`
    segments - the frontend's variable name says nothing about which parameter
    it is. `scoped` means the call went through `ac.path()`, which prefixes
    `/acs/{ac_number}`; without accounting for it every single data call looks
    like a call to a route that does not exist.
    """
    raw, _, query = path.partition("?")
    keys = []
    for part in query.split("&"):
        key = part.split("=")[0].strip()
        # A query string assembled with URLSearchParams leaves nothing to read
        # here; those calls are matched on path alone.
        if key and "{" not in key and "}" not in key:
            keys.append(key)
    if scoped:
        raw = "/acs/{}" + raw
    return raw, keys


def template_shape(path: str) -> str:
    return re.sub(r"\{[^}]*\}", "{}", path)


def shape_candidates(path: str) -> list[str]:
    """The route shapes a frontend path might mean, best guess first.

    A trailing `{}` is usually an optional query suffix, not a path segment:
    `ac.path(`/local-results${seatType ? `?seat_type=${seatType}` : ''}`)`
    collapses to `/acs/{}/local-results{}` and means `/acs/{ac}/local-results`.
    A `{}` between slashes is a real path parameter and is left alone.
    """
    shape = template_shape(path)
    candidates = [shape]
    if shape.endswith("{}") and not shape.endswith("/{}"):
        candidates.append(shape[:-2])
    return candidates


def collect_call_sites() -> list[CallSite]:
    sites: list[CallSite] = []
    if not WEB_SRC.exists():
        return sites
    files = sorted(WEB_SRC.rglob("*.ts")) + sorted(WEB_SRC.rglob("*.tsx"))
    # Application code only: unit tests call made-up endpoints on purpose.
    files = [f for f in files if not ({"__tests__", "test"} & set(f.relative_to(WEB_SRC).parts))
             and ".test." not in f.name]
    for file in files:
        text = file.read_text(encoding="utf-8")
        for number, line in enumerate(text.splitlines(), start=1):
            match = CALL_RE.search(line)
            if not match:
                continue
            argument = collapse_interpolations(match.group("arg"))
            scoped = bool(SCOPED_RE.search(argument))
            inner = PATH_RE.search(argument)
            if not inner:
                continue
            path, query = normalise(inner.group("path"), scoped)
            if not path.startswith("/"):
                continue
            method = "GET" if match.group("csv") else match.group("verb").upper()
            sites.append(CallSite(
                file=str(file.relative_to(REPO_ROOT)).replace("\\", "/"),
                line=number, method=method, path=path, query=query,
            ))
    return sites


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------

def reconcile(routes: list[Route], sites: list[CallSite]) -> dict:
    by_shape: dict[tuple[str, str], list[Route]] = {}
    for route in routes:
        by_shape.setdefault((route.method, template_shape(route.path)), []).append(route)

    matched: dict[str, list[CallSite]] = {r.key: [] for r in routes}
    orphan_calls: list[CallSite] = []
    bad_params: list[tuple[CallSite, Route, list[str]]] = []

    for site in sites:
        candidates: list[Route] = []
        for shape in shape_candidates(site.path):
            candidates = by_shape.get((site.method, shape), [])
            if candidates:
                break
        if not candidates:
            orphan_calls.append(site)
            continue
        route = candidates[0]
        matched[route.key].append(site)
        declared = {p.strip("`?") for p in route.params}
        unknown = [q for q in site.query if q not in declared]
        if unknown:
            bad_params.append((site, route, unknown))

    uncalled = [r for r in routes if not r.is_legacy and not matched[r.key]]
    untyped = [r for r in routes if not r.is_legacy and r.response_model == "dict (untyped)"]
    return {"matched": matched, "orphan_calls": orphan_calls, "uncalled": uncalled,
            "bad_params": bad_params, "untyped": untyped}


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

HEADER = """# docs/design/ENDPOINTS.md

**Generated by `scripts/endpoint_inventory.py`. Do not edit by hand.**
Regenerate with `python scripts/endpoint_inventory.py`; `--check` fails if this
file is out of date, which is what keeps it true.

Required by `FRONTEND_HARDENING.md` 2. It answers three questions that were
previously only answerable by reading every file: what routes exist, who may
call them, and which page calls which.

A `?` on a parameter means it has a default. "Response model" is the pydantic
model FastAPI publishes in the OpenAPI schema; `dict (untyped)` means the
handler returns a bare dict, which generates no schema at all - so
`openapi-typescript` emits `unknown` and the frontend has nothing to check
against.
"""


def render(routes: list[Route], sites: list[CallSite], findings: dict) -> str:
    out = [HEADER]
    live = [r for r in routes if not r.is_legacy]
    legacy = [r for r in routes if r.is_legacy]

    out.append("\n## Summary\n")
    out.append(f"- **{len(live)}** live routes, **{len(legacy)}** legacy redirects")
    out.append(f"- **{len(sites)}** frontend call sites")
    out.append(f"- **{len(findings['untyped'])}** routes with no explicit response model")
    out.append(f"- **{len(findings['uncalled'])}** routes no page calls")
    out.append(f"- **{len(findings['orphan_calls'])}** frontend calls to a route that "
               f"does not exist")
    out.append(f"- **{len(findings['bad_params'])}** calls sending a parameter the route "
               f"does not accept")

    out.append("\n## Routes\n")
    out.append("| Method | Path | Roles | Query / path params | Response model | Called from |")
    out.append("|---|---|---|---|---|---|")
    for route in live:
        callers = findings["matched"][route.key]
        where = "<br>".join(f"`{c.file}:{c.line}`" for c in callers) or "**(none)**"
        params = ", ".join(route.params) or "—"
        model = (route.response_model if route.response_model != "dict (untyped)"
                 else "**dict (untyped)**")
        out.append(f"| {route.method} | `{route.path}` | {route.roles} | {params} | "
                   f"{model} | {where} |")

    out.append("\n## Legacy redirects\n")
    out.append("Pre-multi-AC paths, kept as 308 redirects into `/acs/{ac_number}/…` for "
               "one release. A 308 preserves the method and body; a 302 would turn a POST "
               "into a GET.\n")
    out.append("| Method | Path | Handler |")
    out.append("|---|---|---|")
    for route in legacy:
        out.append(f"| {route.method} | `{route.path}` | `{route.module}.{route.handler}` |")

    out.append("\n## Findings\n")

    out.append("\n### Frontend calls to a route that does not exist\n")
    if findings["orphan_calls"]:
        for site in findings["orphan_calls"]:
            out.append(f"- `{site.file}:{site.line}` calls `{site.method} {site.path}`")
    else:
        out.append("None.")

    out.append("\n### Routes no page calls\n")
    if findings["uncalled"]:
        out.append("Not necessarily wrong - some are for operators, scripts or a page not "
                   "yet built - but each should be either used or removed.\n")
        for route in findings["uncalled"]:
            out.append(f"- `{route.key}` (`{route.module}.{route.handler}`)")
    else:
        out.append("None.")

    out.append("\n### Parameters a page sends that the route does not accept\n")
    if findings["bad_params"]:
        for site, route, unknown in findings["bad_params"]:
            out.append(f"- `{site.file}:{site.line}` sends "
                       f"{', '.join('`' + u + '`' for u in unknown)} to `{route.key}`, "
                       f"which accepts {', '.join(route.params) or 'no parameters'}")
    else:
        out.append("None.")

    out.append("\n### Routes with no explicit response model\n")
    if findings["untyped"]:
        out.append("Each returns a bare `dict`, so its OpenAPI schema is an untyped "
                   "object.\n")
        for route in findings["untyped"]:
            out.append(f"- `{route.key}` (`{route.module}.{route.handler}`)")
    else:
        out.append("None. Every route publishes a model.")

    out.append("")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if docs/design/ENDPOINTS.md is out of date")
    ap.add_argument("--fail-on-findings", action="store_true",
                    help="exit 1 if any finding is non-empty (for a stricter gate)")
    args = ap.parse_args(argv)

    routes = collect_routes()
    sites = collect_call_sites()
    findings = reconcile(routes, sites)
    document = render(routes, sites, findings)

    if args.check:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        if current != document:
            print(f"{OUTPUT.relative_to(REPO_ROOT)} is out of date. Run: "
                  f"python scripts/endpoint_inventory.py", file=sys.stderr)
            return 1
        print(f"{OUTPUT.relative_to(REPO_ROOT)} is up to date "
              f"({len(routes)} routes, {len(sites)} call sites).")
    else:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(document, encoding="utf-8", newline="\n")
        print(f"wrote {OUTPUT.relative_to(REPO_ROOT)}: {len(routes)} routes, "
              f"{len(sites)} call sites")

    for name in ("orphan_calls", "bad_params"):
        if findings[name]:
            print(f"  {len(findings[name])} {name.replace('_', ' ')}", file=sys.stderr)
    print(f"  {len(findings['untyped'])} route(s) with no explicit response model")
    print(f"  {len(findings['uncalled'])} route(s) no page calls")

    if args.fail_on_findings and (findings["orphan_calls"] or findings["bad_params"]):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
