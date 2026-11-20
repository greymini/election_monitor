"""Keep tokens out of access logs: uvicorn logs the full path with its query string, and media links
carry their signed token in the query (players and <img> tags cannot send headers). This filter
drops every query string from access log lines."""
from __future__ import annotations

import logging

ACCESS_LOGGER = "uvicorn.access"


class StripQuery(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str) and "?" in args[2]:
            record.args = args[:2] + (args[2].split("?", 1)[0] + "?…",) + args[3:]
        return True


def install() -> None:
    logger = logging.getLogger(ACCESS_LOGGER)
    if not any(isinstance(f, StripQuery) for f in logger.filters):
        logger.addFilter(StripQuery())
