"""Mint a bearer token for a stub core user (local development only).

    python -m scripts.dev_token usr-principal
"""
from __future__ import annotations

import sys

from pulse.config import ConfigError, load_settings
from pulse.core_adapter.stub import StubCoreAdapter


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: python -m scripts.dev_token <user_id>", file=sys.stderr)
        return 2
    try:
        settings = load_settings()
    except ConfigError as e:
        print(e, file=sys.stderr)
        return 2
    if settings.core_adapter != "stub":
        print("dev_token only works with the stub core adapter", file=sys.stderr)
        return 2
    core = StubCoreAdapter(settings.core_seed_path, jwt_secret=settings.jwt_secret)
    try:
        print(core.issue_token(argv[0]))
    except KeyError:
        print(f"unknown user {argv[0]!r}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
