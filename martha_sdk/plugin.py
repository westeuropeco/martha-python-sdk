"""FastAPI glue for plugin BFFs — the only plugin-specific surface in the SDK.

Optional: requires the ``[fastapi]`` extra. A non-BFF caller (pipeline, script,
cron) imports ``martha_sdk.client`` and never touches this module.

Provides the host plugin-proxy contract: the host forwards the inbound user
JWT (``Authorization``) and tenant (``X-Tenant-Id``); these dependencies
capture them, plus an error handler that maps ``MarthaError`` into the PT/EN
envelope BFFs return.
"""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any

try:
    from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
    from fastapi.responses import JSONResponse
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "martha_sdk.plugin requires FastAPI — install 'martha-python-sdk[fastapi]'"
    ) from exc

from .errors import MarthaAPIError, MarthaError, MarthaUnreachable


def forwarded_authorization(authorization: str | None = Header(default=None)) -> str:
    """Capture the host-forwarded user JWT. 401 when absent."""
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "auth_required",
                "message_pt": "Autenticação necessária.",
                "message_en": "Authorization header required.",
            },
        )
    return authorization


def tenant_id_header(x_tenant_id: str | None = Header(default=None)) -> str:
    """Capture the host-forwarded tenant scope. 400 when absent."""
    if not x_tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "tenant_required",
                "message_pt": "Tenant em falta no pedido.",
                "message_en": "X-Tenant-Id header required.",
            },
        )
    return x_tenant_id


def forwarded_subject(authorization: str = Depends(forwarded_authorization)) -> str:
    """Extract the caller's ``sub`` from the forwarded JWT.

    The host already validated the token; we only read the claim, so we
    base64url-decode the payload without re-verifying the signature. Creator
    attribution MUST come from the token, never from client input.
    """
    try:
        payload_b64 = authorization.split(" ")[-1].split(".")[1]
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        claims = json.loads(base64.urlsafe_b64decode(padded))
    except (IndexError, ValueError, binascii.Error, json.JSONDecodeError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "auth_invalid",
                "message_pt": "Token inválido.",
                "message_en": "Could not parse subject from token.",
            },
        )
    sub = claims.get("sub") or claims.get("preferred_username")
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "auth_invalid",
                "message_pt": "Token sem identificação de utilizador.",
                "message_en": "Token carries no sub/preferred_username claim.",
            },
        )
    return str(sub)


def _envelope(code: str, status_code: int, detail: Any) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message_pt": "Erro ao comunicar com a Martha.",
                "message_en": f"Martha API error: {detail}",
            }
        },
    )


def install_martha_error_handler(app: FastAPI) -> None:
    """Map SDK ``MarthaError``s to the BFF's PT/EN HTTP envelope.

    Without this, a ``MarthaError`` from the client would surface as a raw 500.
    Call once at app build time.
    """

    @app.exception_handler(MarthaUnreachable)
    async def _unreachable(_: Request, exc: MarthaUnreachable) -> JSONResponse:  # noqa: ANN401
        return _envelope("martha_unreachable", status.HTTP_502_BAD_GATEWAY, exc.message)

    @app.exception_handler(MarthaAPIError)
    async def _api_error(_: Request, exc: MarthaAPIError) -> JSONResponse:  # noqa: ANN401
        return _envelope("martha_upstream_error", exc.status_code, exc.detail)

    @app.exception_handler(MarthaError)
    async def _generic(_: Request, exc: MarthaError) -> JSONResponse:  # noqa: ANN401
        return _envelope("martha_error", status.HTTP_502_BAD_GATEWAY, exc.message)


__all__ = [
    "forwarded_authorization",
    "tenant_id_header",
    "forwarded_subject",
    "install_martha_error_handler",
]
