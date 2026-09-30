"""Every translation key resolves, in both languages, through real i18next.

Item 6 of the hardening brief. A raw key was reported rendering on the Overview
page - `health.openReviews` - and a static scan of source against JSON says that
key resolves, because i18next selects `openReviews_other` from a call carrying
`count`. Get the suffixes wrong, or pass `{{n}}` where i18next wants `count`,
and the key falls through to its own name on the page while every file-level
check still passes.

So this runs i18next. The work is in `web/scripts/check-i18n.mjs`, invoked from
here so it runs in the one suite rather than needing a second test command.
Verified against a planted fault: removing `health.openReviews_other` from
hi.json makes it report "resolved to its own name", which is the symptom that
was seen.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess

import pytest

WEB = pathlib.Path(__file__).resolve().parents[1] / "web"
CHECKER = WEB / "scripts" / "check-i18n.mjs"


def test_the_checker_exists():
    assert CHECKER.is_file(), f"{CHECKER} is missing"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_every_key_resolves_in_both_languages():
    if not (WEB / "node_modules" / "i18next").is_dir():
        pytest.skip("web/node_modules/i18next is absent; run npm install in web/")

    result = subprocess.run(
        ["node", str(CHECKER)], cwd=WEB, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, (
        "i18n keys did not resolve:\n" + (result.stderr or result.stdout)
    )
    assert "i18n ok" in result.stdout, result.stdout
