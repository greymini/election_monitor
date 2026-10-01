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
