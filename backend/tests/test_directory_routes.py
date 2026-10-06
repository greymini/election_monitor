"""Directory API routes."""

from __future__ import annotations

from api.routers import directory


def test_directory_router_registered():
    paths = [getattr(r, "path", "") for r in directory.router.routes]
    assert "/directory/panchayats" in paths
    assert "/directory/polling-stations" in paths
    assert "/directory/officials" in paths
    assert "/directory/poll-calendar" in paths
    assert "/directory/panchayats/{area_id}/villages" in paths
