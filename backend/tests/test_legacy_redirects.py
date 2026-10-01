"""Legacy single-AC paths redirect correctly, including behind the nginx /api prefix.

nginx serves the API at /api/ and strips the prefix (frontend/docker/nginx.conf),
so an absolute `Location: /acs/32/...` sent the browser to the frontend's SPA
fallback, which answered index.html with a 200. A relative Location resolves
against the URL the client actually used, so it works with and without the prefix.
"""

from __future__ import annotations

from urllib.parse import urljoin

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers import legacy


@pytest.fixture(scope="module")
def client():
    app = FastAPI()
    app.include_router(legacy.router)
    return TestClient(app)


@pytest.mark.parametrize("old, new", [
    ("/summary", "/acs/32/summary"),
    ("/rolls/changes", "/acs/32/rolls/changes"),
    ("/news/issues?days=7", "/acs/32/news/issues?days=7"),
    ("/results/VS-2024/booths", "/acs/32/results/VS-2024/booths"),
    ("/booths/32-B0042/card", "/acs/32/booths/32-B0042/card"),
])
@pytest.mark.parametrize("prefix", ["", "/api"])
def test_the_location_resolves_to_the_scoped_path(client, old, new, prefix):
    response = client.get(old, follow_redirects=False)
    assert response.status_code == 308
    # Resolve the header the way a browser does, against the URL it requested.
    resolved = urljoin(f"https://monitor.example{prefix}{old}", response.headers["location"])
    assert resolved == f"https://monitor.example{prefix}{new}"


def test_a_post_keeps_its_method(client):
    assert client.post("/scenario", json={}, follow_redirects=False).status_code == 308


def test_an_old_style_booth_uid_gets_its_ac_prefix(client):
    response = client.get("/booths/B0042/card", follow_redirects=False)
    assert urljoin("https://m.example/booths/B0042/card", response.headers["location"]) \
        == "https://m.example/acs/32/booths/32-B0042/card"


def test_a_query_parameter_cannot_choose_the_target(client):
    """`_path` was a default argument, which FastAPI exposes as a query param."""
    response = client.get("/summary?_path=/admin/usage", follow_redirects=False)
    resolved = urljoin("https://m.example/summary", response.headers["location"])
    assert resolved.startswith("https://m.example/acs/32/summary")
