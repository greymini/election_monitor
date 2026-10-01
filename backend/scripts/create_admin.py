"""Create the first admin user (LLD 9).

    python -m scripts.create_admin --phone 9999999999 --name "Analyst" --role admin

With AUTH_MODE=password a password is required; generate a strong one and hand
it over out of band. The password is never logged, and only its bcrypt hash is
stored.
"""

from __future__ import annotations

import argparse
import getpass
import secrets
import string
import sys
from pathlib import Path

# Run as a file (`python scripts/create_admin.py`), sys.path[0] is scripts/,
# so `import common` failed; `python -m scripts.create_admin` worked. Both do now.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.db import query_one  # noqa: E402
from common.logging_setup import get_logger, setup_logging

log = get_logger(__name__)


def generate_password(length: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Create or update an application user")
    ap.add_argument("--phone", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--role", default="admin", choices=["admin", "strategist", "block"])
    ap.add_argument("--block", type=int, help="block_id, required for the block role")
    ap.add_argument("--generate-password", action="store_true",
                    help="generate and print a password instead of prompting")
    ap.add_argument("--budget", type=int, help="daily token budget override")
    args = ap.parse_args(argv)

    setup_logging()

    if args.role == "block" and args.block is None:
        ap.error("--block is required for the block role")

    from api.deps import hash_password

    if args.generate_password:
        password = generate_password()
    else:
        password = getpass.getpass("Password: ")
        if password != getpass.getpass("Repeat: "):
            log.error("passwords do not match")
            return 2
    if len(password) < 8:
        log.error("password must be at least 8 characters")
        return 2

    budget = args.budget or (60_000 if args.role == "block" else 150_000)
    row = query_one(
        "INSERT INTO app_user (phone, name, role, block_id, password_hash, daily_token_budget) "
        "VALUES (%s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (phone) DO UPDATE SET name = EXCLUDED.name, role = EXCLUDED.role, "
        "block_id = EXCLUDED.block_id, password_hash = EXCLUDED.password_hash, "
        "daily_token_budget = EXCLUDED.daily_token_budget, is_active = true "
        "RETURNING user_id",
        (args.phone, args.name, args.role, args.block, hash_password(password), budget),
    )
    log.info("user %s (%s, role=%s, budget=%s tokens/day) ready",
             row["user_id"], args.phone, args.role, f"{budget:,}")
    if args.generate_password:
        print(f"\nPassword for {args.phone}: {password}\n"
              f"Give this to the user out of band. It is not stored anywhere in plain text.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
