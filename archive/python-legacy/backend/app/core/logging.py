"""Structured JSON logging with structlog."""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

# ── SRE-23: drop-keys allow-list ────────────────────────────────────────────
#
# The legacy ``main._redact()`` regex still scrubs sensitive substrings out
# of free-form log MESSAGES (uvicorn access log lines, raw exception
# strings). It does not, however, intercept structured ``structlog`` events
# where a sensitive value is bound directly as a kwarg
# (``logger.info(..., api_key="ak_secret")``). Modern structlog hygiene
# prefers a processor that drops sensitive keys before they reach the
# JSON renderer — defence in depth on top of the regex layer.
#
# This is intentionally a key-name allow-list rather than a value pattern
# match: the cost of hashing every str value on every log record is non-
# trivial on the hot path, and the failure mode of a missed key is much
# rarer than the failure mode of a missed value pattern. Structured logs
# in this codebase use a small, well-known set of field names; we drop
# any key that matches one of the well-known sensitive names.
_SENSITIVE_LOG_KEYS: frozenset[str] = frozenset(
    {
        "api_key",
        "apikey",
        "api_key_value",
        "token",
        "access_token",
        "refresh_token",
        "bot_token",
        "password",
        "passphrase",
        "secret",
        "client_secret",
        "jwt_secret",
        "db_encryption_key",
        "key",
        "plaintext_key",
        "code",
        "state",
    }
)
_REDACTED = "REDACTED"


def drop_sensitive_keys_processor(
    logger: Any, method_name: str, event_dict: dict[str, Any]  # noqa: ARG001
) -> dict[str, Any]:
    """Replace values for sensitive structlog keys with ``"REDACTED"``.

    Keeps the key (so callers can see *that* a redaction happened) but
    discards the value before any downstream processor (JSON renderer,
    file handler) gets it. Whitelist-style — only top-level keys are
    inspected; nested dicts are left alone because the only documented
    sensitive nested shape (admin-audit ``meta``) is already filtered
    at the source.
    """
    for k in list(event_dict.keys()):
        if k.lower() in _SENSITIVE_LOG_KEYS:
            event_dict[k] = _REDACTED
    return event_dict


def configure_logging(level: str = "INFO") -> None:
    log_level = getattr(logging, level.upper(), logging.INFO)

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            # SRE-23: drop sensitive structured keys before render.
            drop_sensitive_keys_processor,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name) if name else structlog.get_logger()
