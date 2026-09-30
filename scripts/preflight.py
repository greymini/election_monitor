#!/usr/bin/env python
"""Check that this machine can run ingestion before you start a long load.

Written for the laptop case: a analyst with the repo, a remote DATABASE_URL, and
Form 20 PDFs in object storage. Every failure mode there is silent or slow -
psycopg's pool waits 30 seconds before it admits it cannot connect, a missing
PostGIS extension does not surface until migration 0001, and a wrong S3 key
looks exactly like a document that was never fetched. This prints all of it in
one go, in dependency order, and says what to do about each.

    python scripts/preflight.py
    python scripts/preflight.py --skip-storage     # database only

Exit code 0 when everything needed for ingestion is present, 1 otherwise.
Nothing is written; it is safe to run against production.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass

OK = "ok"
WARN = "warn"
FAIL = "fail"

MARK = {OK: "[ ok ]", WARN: "[warn]", FAIL: "[FAIL]"}


@dataclass
class Check:
    name: str
    status: str
    detail: str
    fix: str = ""


def check_env() -> list[Check]:
    out = []
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        out.append(Check(
            "DATABASE_URL", FAIL, "not set",
            "export DATABASE_URL='postgresql://user:pass@host:5432/giridih?sslmode=require'",
        ))
        return out

    # Do not print the password back at the operator.
    redacted = url
    if "@" in url and "//" in url:
        scheme, rest = url.split("//", 1)
        creds, host = rest.split("@", 1)
        user = creds.split(":", 1)[0]
        redacted = f"{scheme}//{user}:***@{host}"
    out.append(Check("DATABASE_URL", OK, redacted))

    remote = not any(h in url for h in ("@localhost", "@127.0.0.1", "@db:", "@db/"))
    if remote and "sslmode=" not in url:
        out.append(Check(
            "TLS to the database", WARN,
            "a remote host with no sslmode - the connection may be in cleartext",
            "append ?sslmode=require (or verify-full with a CA bundle) to DATABASE_URL",
        ))
    return out


def check_database() -> list[Check]:
    out: list[Check] = []
    try:
        from common.db import query, query_one
    except ImportError as exc:
        return [Check("psycopg", FAIL, str(exc),
                      "pip install -r requirements-worker.txt")]

    try:
        row = query_one("SELECT version() AS v, current_database() AS db, current_user AS u")
    except Exception as exc:
        return [Check("connect", FAIL, f"{type(exc).__name__}: {str(exc)[:160]}",
                      "check the host, port, credentials and that the server allows this IP")]

    version = str(row["v"]).split(" on ")[0]
    out.append(Check("connect", OK, f"{version}, db={row['db']}, user={row['u']}"))

    major = 0
    parts = version.split()
    if len(parts) > 1:
        try:
            major = int(parts[1].split(".")[0])
        except ValueError:
            pass
    if major and major < 15:
        out.append(Check(
            "server version", WARN, f"PostgreSQL {major}",
            "16 is the target; 15 is the floor for UNIQUE NULLS NOT DISTINCT, "
            "which the Form 20 candidate key needs",
        ))

    # Extensions. postgis is needed by migration 0001 and by GET /booths, which
    # calls ST_X/ST_Y; vector is needed by the news embedding columns.
    try:
        installed = {r["extname"] for r in query("SELECT extname FROM pg_extension")}
        available = {r["name"] for r in query("SELECT name FROM pg_available_extensions")}
    except Exception as exc:
        out.append(Check("extensions", WARN, f"could not read: {str(exc)[:120]}"))
        return out

    for name, why in (("postgis", "migration 0001 and GET /booths (ST_X/ST_Y)"),
                      ("vector", "news embedding columns")):
        if name in installed:
            out.append(Check(f"extension {name}", OK, "installed"))
        elif name in available:
            out.append(Check(f"extension {name}", WARN, f"available but not created ({why})",
                             "python -m db.apply_migrations will create it"))
        else:
            out.append(Check(
                f"extension {name}", FAIL, f"not available on this server ({why})",
                "use an image with it - docker/Dockerfile.db builds "
                "pgvector/pgvector:pg16 plus postgresql-16-postgis-3 - or install the "
                "package on the managed instance",
            ))

    # Migration state.
    try:
        applied = query("SELECT filename FROM schema_migration ORDER BY filename")
    except Exception:
        out.append(Check("migrations", WARN, "schema_migration does not exist - nothing applied",
                         "python -m db.apply_migrations --seed"))
        return out

    from pathlib import Path

    on_disk = sorted(p.name for p in
                     (Path(__file__).resolve().parents[1] / "db" / "migrations").glob("*.sql"))
    done = {r["filename"] for r in applied}
    pending = [name for name in on_disk if name not in done]
    if pending:
        out.append(Check("migrations", FAIL,
                         f"{len(done)} applied, {len(pending)} pending: {', '.join(pending[:4])}"
                         + (" ..." if len(pending) > 4 else ""),
                         "python -m db.apply_migrations"))
    else:
        out.append(Check("migrations", OK, f"all {len(done)} applied"))

    # Is there anything to serve?
    try:
        counts = query_one(
            "SELECT (SELECT COUNT(*) FROM booth) AS booths, "
            "(SELECT COUNT(*) FROM result_booth) AS results, "
            "(SELECT COUNT(*) FROM source_doc) AS docs"
        )
        out.append(Check("data", OK if counts["booths"] else WARN,
                         f"{counts['booths']} booth(s), {counts['results']} result row(s), "
                         f"{counts['docs']} source document(s)",
                         "" if counts["booths"] else
                         "load a PS list first: python -m ingest.parse_pslist <pdf> "
                         "--election VS-2024 --load --anchor --block 2"))
    except Exception as exc:
        out.append(Check("data", WARN, f"could not count: {str(exc)[:120]}"))

    return out


def check_storage() -> list[Check]:
    out: list[Check] = []
    from common.config import DOC_KINDS, get_settings
    from common.storage import ROLL_KINDS, StorageError, backend_name_for_kind

    settings = get_settings()
    out.append(Check("STORAGE_BACKEND", OK, settings.storage_backend))

    routing: dict[str, str] = {}
    refused: list[str] = []
    for kind in DOC_KINDS:
        try:
            routing[kind] = backend_name_for_kind(kind)
        except StorageError as exc:
            refused.append(kind)
            out.append(Check(f"routing {kind}", FAIL, str(exc)[:200],
                             f"unset STORAGE_BACKEND_{kind.upper()} or set it to local"))
    if routing:
        summary = ", ".join(f"{k}={v}" for k, v in sorted(routing.items()))
        out.append(Check("routing", OK, summary))

    # The invariant, restated so it appears in the operator's output. A refused
    # roll kind means the guard is working and the configuration is wrong: the
    # rolls are not going anywhere, but somebody believes they are, and every
    # roll load will abort until that belief is corrected.
    roll_refused = sorted(set(refused) & ROLL_KINDS)
    if roll_refused:
        out.append(Check(
            "rolls stay local", WARN,
            f"{', '.join(roll_refused)} configured for a remote backend and refused - "
            "no roll has left the host, but roll loads will abort",
            "correct the configuration above; the guard is not the problem",
        ))
    else:
        out.append(Check("rolls stay local", OK,
                         "roll_mother and roll_supplement are local"))

    if "s3" in routing.values():
        if not settings.s3_bucket:
            out.append(Check("S3_BUCKET", FAIL, "not set but a kind is routed to s3",
                             "set S3_BUCKET, or set STORAGE_BACKEND=local"))
            return out
        try:
            import boto3  # noqa: F401
        except ImportError:
            out.append(Check("boto3", FAIL, "not installed but a kind is routed to s3",
                             "pip install -r requirements-worker.txt"))
            return out

        from common.storage import S3Storage
        try:
            store = S3Storage()
            keys = store.list()
            out.append(Check("s3 reachable", OK,
                             f"{settings.s3_endpoint or 'aws'} bucket={settings.s3_bucket} "
                             f"prefix={settings.s3_prefix or '(none)'}, {len(keys)} object(s)"))
        except Exception as exc:
            out.append(Check("s3 reachable", FAIL, f"{type(exc).__name__}: {str(exc)[:160]}",
                             "check S3_ENDPOINT, S3_BUCKET and the credentials"))

    local_root = settings.raw_dir
    try:
        local_root.mkdir(parents=True, exist_ok=True)
        probe = local_root / ".preflight"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        out.append(Check("RAW_DIR writable", OK, str(local_root)))
    except OSError as exc:
        out.append(Check("RAW_DIR writable", FAIL, f"{local_root}: {exc}",
                         "set RAW_DIR to a directory this account can write"))
    return out


def check_tools() -> list[Check]:
    """Optional binaries. Missing ones limit what can be parsed, not whether
    anything runs, so these are warnings."""
    import shutil

    out: list[Check] = []
    for module, why in (("pdfplumber", "PDF text extraction - required for every parser"),):
        try:
            __import__(module)
            out.append(Check(module, OK, "installed"))
        except ImportError:
            out.append(Check(module, FAIL, f"not installed ({why})",
                             "pip install -r requirements-worker.txt"))
    for binary, why in (("pdftoppm", "rasterising pages for OCR (poppler-utils)"),
                        ("tesseract", "OCR for scanned pages")):
        if shutil.which(binary):
            out.append(Check(binary, OK, "on PATH"))
        else:
            out.append(Check(binary, WARN, f"not on PATH ({why})",
                             "only needed for PDFs with no text layer"))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skip-storage", action="store_true", help="database checks only")
    ap.add_argument("--skip-tools", action="store_true", help="skip the PDF toolchain checks")
    args = ap.parse_args(argv)

    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

    checks: list[Check] = []
    checks += check_env()
    if not any(c.name == "DATABASE_URL" and c.status == FAIL for c in checks):
        checks += check_database()
    if not args.skip_storage:
        checks += check_storage()
    if not args.skip_tools:
        checks += check_tools()

    width = max(len(c.name) for c in checks)
    print()
    for check in checks:
        print(f"  {MARK[check.status]} {check.name.ljust(width)}  {check.detail}")
        if check.fix and check.status != OK:
            print(f"         {' ' * width}  -> {check.fix}")
    print()

    failures = [c for c in checks if c.status == FAIL]
    warnings = [c for c in checks if c.status == WARN]
    if failures:
        print(f"{len(failures)} blocking problem(s), {len(warnings)} warning(s). "
              "Ingestion will not work until the failures are fixed.")
        return 1
    print(f"Ready for ingestion. {len(warnings)} warning(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
