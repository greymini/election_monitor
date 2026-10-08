"""Each Docker image contains every local package its code imports.

The worker image copied common, ingest, news, analytics, worker and db - but
news/label_batch.py and news/label_collect.py import chatbot, so both news
labelling jobs died with ModuleNotFoundError in the container while every test
on a developer machine (which has the whole tree) passed.
"""

from __future__ import annotations

import ast
import re

import pytest

from tests.paths import BACKEND

# Imports an image deliberately goes without, each inside a try/except that
# degrades rather than fails. The API image needs `news` (/news and
# /news/summary import news.crawl_rss; leaving it out was a 500 on Railway);
# news.embed's model is still worker-only and the API ranks by date without it.
# The worker uses chatbot.llm and chatbot.budget for news labelling; only the
# chat tools (never run by the worker) import api.booth_card, lazily.
OPTIONAL = {("Dockerfile.worker", "api")}

LOCAL = {p.name for p in BACKEND.iterdir() if p.is_dir() and (p / "__init__.py").exists()}


def _copied(dockerfile: str) -> set[str]:
    text = (BACKEND / "docker" / dockerfile).read_text(encoding="utf-8")
    return {m.group(1) for m in re.finditer(r"^COPY\s+(\w+)\s+\./\1\s*$", text, re.M)}


def _imports(package: str) -> dict[str, set[str]]:
    """Local top-level packages imported anywhere in `package`, with where."""
    found: dict[str, set[str]] = {}
    for path in (BACKEND / package).rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module]
            for name in names:
                top = name.split(".")[0]
                if top in LOCAL and top != package:
                    found.setdefault(top, set()).add(str(path.relative_to(BACKEND)))
    return found


@pytest.mark.parametrize("dockerfile", ["Dockerfile.api", "Dockerfile.worker"])
def test_every_imported_local_package_is_copied(dockerfile):
    copied = _copied(dockerfile)
    assert copied, f"no COPY lines parsed from {dockerfile}"
    missing = {}
    for package in copied:
        for imported, where in _imports(package).items():
            if (imported not in copied and imported not in {"tests", "fixtures"}
                    and (dockerfile, imported) not in OPTIONAL):
                missing.setdefault(imported, set()).update(where)
    assert not missing, f"{dockerfile} lacks {sorted(missing)} imported by " + \
        "; ".join(f"{k}: {sorted(v)[:3]}" for k, v in missing.items())


def test_the_worker_creates_its_data_directories_before_handing_them_over():
    """Audit A3: volumes mounted at paths absent from the image are created by
    Docker as root, and the worker (uid 10002) then cannot write to them."""
    text = (BACKEND / "docker" / "Dockerfile.worker").read_text(encoding="utf-8")
    for path in ("/data/raw", "/data/ocr", "/data/backups"):
        assert path in text, path
