"""scripts/dev_geo.py's safety check reads the database name correctly."""

from __future__ import annotations

import pytest

from scripts.dev_geo import database_name


@pytest.mark.parametrize("url, name", [
    ("postgresql://postgres:@localhost:5432/giridih_dev", "giridih_dev"),
    # macOS / Linux pgserver: the socket directory is in the query string, and
    # the old rsplit("/") read it ("pgdata") as the database, so the dev stack's
    # geo step refused to run there.
    ("postgresql://postgres:@/giridih_dev?host=/Users/x/repo/backend/.devstack/pgdata",
     "giridih_dev"),
    ("postgresql://u:p@db:5432/giridih", "giridih"),
])
def test_database_name(url, name):
    assert database_name(url) == name
