"""Exceptions raised by the Martha Python SDK.

The client raises these — NOT ``fastapi.HTTPException`` — so it stays usable
outside a web framework (scripts, pipelines, tests). The optional
``martha_sdk.plugin`` layer translates them into HTTP responses for BFFs.
"""

from __future__ import annotations

from typing import Any


class MarthaError(Exception):
    """Base for everything this SDK raises."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class MarthaConfigError(MarthaError):
    """The SDK was used without required configuration (e.g. SA credentials)."""


class MarthaUnreachable(MarthaError):
    """The Martha host could not be contacted (connection/DNS/timeout)."""


class MarthaAPIError(MarthaError):
    """Martha returned an HTTP error. ``detail`` is the parsed upstream body."""

    def __init__(self, status_code: int, detail: Any, message: str | None = None) -> None:
        super().__init__(message or f"Martha API error {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


class MarthaBackpressure(MarthaAPIError):
    """Tenant ingestion cap (HTTP 429) persisted across all retries."""
