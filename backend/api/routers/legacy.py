"""308 redirects from the pre-multi-AC paths to `/acs/32/...`, for one release.

Every data route moved under `/acs/{ac_number}/`. The old paths are in
`RUN.md`, in the LLD's API table, in anyone's bookmarks and in whatever scripts
the analyst team has already written, so breaking them outright would be gratuitous.

308 rather than 301 or 302 on purpose: 308 is the only redirect status that
requires the client to keep the method and the body. `POST /scenario` and
`POST /ground-reports` both matter here, and a 301 or 302 would let a client
turn them into GETs - which would look like a silently ignored request rather
than an error.

AC-32 is the target because it is the only constituency that had data before the
change, so every old URL meant Giridih by construction.

These are removed one release after the multi-AC change ships. They are listed
in `docs/RUNBOOK.md` so that removal is a deliberate act rather than a surprise.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from starlette.status import HTTP_308_PERMANENT_REDIRECT

from common.logging_setup import get_logger

log = get_logger(__name__)
router = APIRouter(tags=["legacy"], include_in_schema=False)

DEFAULT_AC = 32

# Old path -> path under /acs/{ac}/. Paths with parameters are handled by the
# catch-all below; these are the fixed ones.
MOVED = (
    "/summary",
    "/booths",
    "/rolls/changes",
    "/rolls/revisions",
    "/caste",
    "/transfer",
    "/local-results",
    "/priority",
    "/areas",
    "/knowledge-cards",
    "/news",
    "/news/issues",
    "/ground-reports",
    "/scenario",
)


def _redirect(request: Request, target: str) -> RedirectResponse:
    """308 to `/acs/32{target}`, as a *relative* Location.

    nginx serves the API under /api/ and strips the prefix, so an absolute
    `/acs/32/...` sent a browser to the frontend's SPA fallback (index.html,
    200). A relative reference climbs out of the request's own path and is
    resolved by the client against the URL it actually used - so it lands on
    /api/acs/32/... behind the proxy and on /acs/32/... without it.
    """
    depth = request.url.path.rstrip("/").count("/") - 1
    query = request.url.query
    location = ("../" * depth) + f"acs/{DEFAULT_AC}{target}" + (f"?{query}" if query else "")
    log.info("legacy path %s -> %s", request.url.path, location)
    return RedirectResponse(location, status_code=HTTP_308_PERMANENT_REDIRECT)


def _register(path: str) -> None:
    # `path` is closed over, not a default argument: FastAPI exposes every
    # handler parameter, so `_path: str = path` was a query parameter a caller
    # could set to choose the redirect target.
    async def handler(request: Request) -> RedirectResponse:
        return _redirect(request, path)

    # Both verbs for every path: a GET-only route would answer a legacy POST
    # with 405, which is a less useful signal than a redirect.
    router.add_api_route(path, handler, methods=["GET", "POST"],
                         include_in_schema=False,
                         name=f"legacy_{path.strip('/').replace('/', '_')}")


for _path in MOVED:
    _register(_path)


@router.api_route("/booths/{booth_uid}/card", methods=["GET"], include_in_schema=False)
async def legacy_booth_card(booth_uid: str, request: Request) -> RedirectResponse:
    # Pre-multi-AC uids were `B0042`; they are `32-B0042` now (0014).
    if not booth_uid.startswith(f"{DEFAULT_AC}-"):
        booth_uid = f"{DEFAULT_AC}-{booth_uid}"
    return _redirect(request, f"/booths/{booth_uid}/card")


@router.api_route("/results/{election_label}/booths", methods=["GET"], include_in_schema=False)
async def legacy_results_booths(election_label: str, request: Request) -> RedirectResponse:
    return _redirect(request, f"/results/{election_label}/booths")


@router.api_route("/results/{election_label}/areas", methods=["GET"], include_in_schema=False)
async def legacy_results_areas(election_label: str, request: Request) -> RedirectResponse:
    return _redirect(request, f"/results/{election_label}/areas")
