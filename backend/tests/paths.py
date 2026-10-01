"""Where things are, for tests that read files rather than import modules.

The repository is split into `backend/` (this package's parent) and
`frontend/`. Tests that check frontend source or shared config files resolve
them from here, so a future move only changes this file.
"""

from __future__ import annotations

from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND.parent
FRONTEND = REPO_ROOT / "frontend"
FRONTEND_SRC = FRONTEND / "src"
MIGRATIONS = BACKEND / "db" / "migrations"


def frontend_sources(pattern: str = "*.ts*"):
    """Application source under frontend/src: not tests, test helpers or data.

    Unit tests call made-up endpoints and render made-up values on purpose, so
    the static scans (endpoint table, hardcoded numbers, scoped paths) must not
    read them as application code.
    """
    for path in sorted(FRONTEND_SRC.rglob(pattern)):
        parts = set(path.relative_to(FRONTEND_SRC).parts)
        if parts & {"__tests__", "test"} or ".test." in path.name:
            continue
        yield path
