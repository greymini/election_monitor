#!/usr/bin/env python
"""Delete any extracted page text left on disk by a roll parse (audit C3).

Run this once on any host that parsed a roll before the fix, and after any
incident. `extract_pdf` no longer caches roll pages - `parse_roll` passes
`cache=False` - but a host that ran the old code still has the text, and it does
not expire.

What it deletes: files under `OCR_DIR` whose content matches the EPIC pattern,
and the cache directory they sit in. What it never touches: `raw/`. Those are the
source PDFs, they are the audit trail, and they are expected to contain personal
data - the guarantee is that nothing *derived* from them is kept, not that the
originals do not exist.

    python scripts/purge_roll_cache.py              # report only
    python scripts/purge_roll_cache.py --delete     # actually remove

Report-only by default, because this deletes data and the operator should see
what it found first.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.config import get_settings  # noqa: E402
from common.logging_setup import get_logger, setup_logging  # noqa: E402
from common.pii import EPIC, scan_file  # noqa: E402

log = get_logger(__name__)


def suspect_dirs(ocr_dir: Path) -> dict[Path, int]:
    """Cache directories holding EPIC-shaped text, and how many files each.

    Directories rather than files: `extract_pdf` writes one JSON per page under
    a per-document directory, so a roll leaves hundreds of files in one place
    and removing the directory is both quicker and less likely to leave an
    orphan behind.
    """
    found: dict[Path, int] = {}
    if not ocr_dir.exists():
        return found

    for path in sorted(ocr_dir.rglob("*.json")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:  # pragma: no cover
            continue
        if EPIC.search(text):
            # The document's cache directory, which is a direct child of OCR_DIR.
            try:
                top = path.relative_to(ocr_dir).parts[0]
            except ValueError:  # pragma: no cover
                continue
            key = ocr_dir / top
            found[key] = found.get(key, 0) + 1
    return found


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--delete", action="store_true",
                    help="remove what is found (default is to report only)")
    ap.add_argument("--all", action="store_true",
                    help="remove every cache directory, not only those matching EPIC. "
                         "Use after an incident, when you would rather re-extract "
                         "everything than reason about what leaked.")
    args = ap.parse_args(argv)

    setup_logging()
    ocr_dir = Path(get_settings().ocr_dir)
    log.info("scanning %s", ocr_dir)

    if not ocr_dir.exists():
        log.info("no cache directory at %s - nothing to purge", ocr_dir)
        return 0

    if args.all:
        targets = {d: len(list(d.rglob('*'))) for d in sorted(ocr_dir.iterdir()) if d.is_dir()}
        reason = "every cache directory (--all)"
    else:
        targets = suspect_dirs(ocr_dir)
        reason = "directories containing EPIC-shaped text"

    if not targets:
        log.info("clean: no %s under %s", reason, ocr_dir)
        return 0

    total_files = sum(targets.values())
    log.warning("found %d %s, %d file(s) in total:", len(targets), reason, total_files)
    for path, count in sorted(targets.items()):
        log.warning("  %s (%d file(s))", path, count)

    if not args.delete:
        log.warning("report only. Re-run with --delete to remove these.")
        # Exit non-zero so a CI or pre-flight check treats a find as a failure.
        return 1

    removed = 0
    for path in sorted(targets):
        try:
            shutil.rmtree(path)
            removed += 1
            log.info("removed %s", path)
        except OSError as exc:
            log.error("could not remove %s: %s", path, exc)

    log.info("removed %d of %d directory/ies", removed, len(targets))
    log.info("Now confirm with: python -m ingest.validate --privacy")

    # Anything left behind is still a leak, so report it as one.
    leftovers = [f for f in (scan_file(p) for p in ocr_dir.rglob("*.json")) if f]
    if leftovers:
        log.error("%d file(s) under %s still match a personal-data pattern",
                  len(leftovers), ocr_dir)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
