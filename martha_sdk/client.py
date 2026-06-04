"""MarthaClient — a generic Python client for the Martha host API.

Two auth strategies cover every caller:

- ``BearerAuth`` — forward an inbound user/admin JWT (the plugin BFF pattern:
  Martha sees the real operator). Construct with the full ``Authorization``
  header value or via ``BearerAuth.from_token``.
- ``ServiceAccountAuth`` — client_credentials grant against Keycloak, token
  cached until ~expiry. For programmatic callers (workflow trigger, pipelines,
  cron). The token's ``tenant_id`` claim scopes tenant-bound operations.

``MARTHA_API_URL`` convention includes the ``/api`` base, so paths passed to
the client are ``/admin/...`` / ``/service/...``.

Zero runtime dependencies (stdlib ``urllib``). Raises ``martha_sdk.errors`` —
never ``fastapi.HTTPException``.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any, Protocol

from .errors import (
    MarthaAPIError,
    MarthaBackpressure,
    MarthaConfigError,
    MarthaError,
    MarthaUnreachable,
)


# ---------------------------------------------------------------------------
# Auth strategies
# ---------------------------------------------------------------------------
class Auth(Protocol):
    def authorization(self) -> str:
        """Return the value for the ``Authorization`` header."""
        ...


class BearerAuth:
    """Forward a bearer token. Pass the full header value or use from_token."""

    def __init__(self, authorization: str) -> None:
        if not authorization:
            raise MarthaConfigError("BearerAuth requires a non-empty Authorization value")
        self._authorization = authorization

    @classmethod
    def from_token(cls, token: str) -> "BearerAuth":
        return cls(f"Bearer {token}")

    def authorization(self) -> str:
        return self._authorization


class ServiceAccountAuth:
    """Keycloak client_credentials grant, cached until ~30s before expiry."""

    def __init__(self, token_url: str, client_id: str, client_secret: str) -> None:
        if not (token_url and client_id and client_secret):
            raise MarthaConfigError(
                "ServiceAccountAuth requires token_url, client_id, client_secret"
            )
        self._token_url = token_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._token: str | None = None
        self._exp: float = 0.0

    def authorization(self) -> str:
        now = time.time()
        if self._token and self._exp - 30 > now:
            return f"Bearer {self._token}"
        body = urllib.parse.urlencode(
            {
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            self._token_url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            raise MarthaAPIError(exc.code, detail, "Service-account token request failed") from exc
        except urllib.error.URLError as exc:
            raise MarthaUnreachable(f"Could not reach token endpoint: {exc.reason}") from exc
        token = payload.get("access_token")
        if not token:
            raise MarthaAPIError(502, payload, "Token endpoint returned no access_token")
        self._token = token
        self._exp = now + float(payload.get("expires_in", 300))
        return f"Bearer {token}"


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------
_DEFAULT_UPLOAD_BACKOFF_S: tuple[int, ...] = (1, 2, 4)


class MarthaClient:
    """A configured client for one Martha host + one auth identity.

    For per-request plugin use, build one per request with the forwarded JWT.
    For service callers, build one with ``ServiceAccountAuth`` and reuse it.
    """

    def __init__(
        self,
        base_url: str,
        auth: Auth,
        *,
        tenant_id: str | None = None,
        timeout: int = 30,
    ) -> None:
        if not base_url:
            raise MarthaConfigError("MarthaClient requires a base_url (incl. /api)")
        self.base_url = base_url.rstrip("/")
        self.auth = auth
        self.tenant_id = tenant_id
        self.timeout = timeout

    # ---- convenience constructors -------------------------------------------
    @classmethod
    def for_user(cls, base_url: str, authorization: str, tenant_id: str, **kw: Any) -> "MarthaClient":
        return cls(base_url, BearerAuth(authorization), tenant_id=tenant_id, **kw)

    @classmethod
    def for_service_account(
        cls,
        base_url: str,
        *,
        token_url: str,
        client_id: str,
        client_secret: str,
        tenant_id: str | None = None,
        **kw: Any,
    ) -> "MarthaClient":
        auth = ServiceAccountAuth(token_url, client_id, client_secret)
        return cls(base_url, auth, tenant_id=tenant_id, **kw)

    # ---- low-level ----------------------------------------------------------
    def _headers(self, tenant_id: str | None, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {"Authorization": self.auth.authorization()}
        tid = tenant_id or self.tenant_id
        if tid:
            headers["X-Tenant-Id"] = tid
        if extra:
            headers.update(extra)
        return headers

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict | None = None,
        params: dict | None = None,
        tenant_id: str | None = None,
        timeout: int | None = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            if clean:
                url = f"{url}?{urllib.parse.urlencode(clean)}"
        data = None
        extra: dict[str, str] = {}
        if json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
            extra["Content-Type"] = "application/json"
        req = urllib.request.Request(
            url, data=data, method=method, headers=self._headers(tenant_id, extra)
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                raw = resp.read()
                return json.loads(raw.decode("utf-8")) if raw else None
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            try:
                detail = json.loads(body).get("detail", body)
            except Exception:
                detail = body
            raise MarthaAPIError(exc.code, detail) from exc
        except urllib.error.URLError as exc:
            raise MarthaUnreachable(f"Could not reach Martha API: {exc.reason}") from exc

    # ---- collections + documents -------------------------------------------
    def create_collection(
        self,
        name: str,
        *,
        parent_collection_id: str | None = None,
        description: str | None = None,
        tenant_id: str | None = None,
    ) -> str:
        body: dict[str, Any] = {"name": name}
        if parent_collection_id:
            body["parent_collection_id"] = parent_collection_id
        if description:
            body["description"] = description
        created = self.request("POST", "/admin/collections", json_body=body, tenant_id=tenant_id)
        if not isinstance(created, dict) or not created.get("id"):
            raise MarthaAPIError(502, created, "create-collection returned no id")
        return str(created["id"])

    def list_collections(self, *, tenant_id: str | None = None) -> list[dict[str, Any]]:
        """Return the tenant's collections (id, name, slug, parent_collection_id…).

        Used to resolve a placement *path* back to a collection id — the path
        representation mirrors ``core.drive_tools.list_drive_folders``:
        ``"/" + "/".join(collection names from the root)``.
        """
        result = self.request("GET", "/admin/collections", tenant_id=tenant_id)
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            for key in ("collections", "items"):
                if isinstance(result.get(key), list):
                    return result[key]
        return []

    def upload_document(
        self,
        collection_id: str,
        *,
        filename: str,
        content: bytes,
        content_type: str = "application/octet-stream",
        metadata: dict | None = None,
        tenant_id: str | None = None,
        timeout: int = 60,
        backoff_s: tuple[int, ...] = _DEFAULT_UPLOAD_BACKOFF_S,
    ) -> str:
        """Multipart upload; retries the per-tenant 429 ingestion cap with backoff."""
        boundary = f"----MarthaSDK{uuid.uuid4().hex}"
        sep = f"--{boundary}".encode("ascii")
        safe = filename.replace('"', "")
        parts: list[bytes] = [
            sep,
            f'Content-Disposition: form-data; name="file"; filename="{safe}"'.encode("utf-8"),
            f"Content-Type: {content_type or 'application/octet-stream'}".encode("utf-8"),
            b"",
            content,
        ]
        if metadata:
            parts.extend(
                [
                    sep,
                    b'Content-Disposition: form-data; name="metadata"',
                    b"",
                    json.dumps(metadata, separators=(",", ":")).encode("utf-8"),
                ]
            )
        parts.extend([f"--{boundary}--".encode("ascii"), b""])
        body = b"\r\n".join(parts)
        url = f"{self.base_url}/admin/collections/{collection_id}/documents"
        headers = self._headers(
            tenant_id,
            {
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Content-Length": str(len(body)),
            },
        )

        last: urllib.error.HTTPError | None = None
        for delay in (0, *backoff_s):
            if delay:
                time.sleep(delay)
            req = urllib.request.Request(url, data=body, method="POST", headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    raw = resp.read().decode("utf-8")
                    payload = json.loads(raw) if raw else {}
                    break
            except urllib.error.HTTPError as exc:
                last = exc
                if exc.code != 429:
                    detail = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
                    raise MarthaAPIError(exc.code, detail, "Document upload failed") from exc
            except urllib.error.URLError as exc:
                raise MarthaUnreachable(f"Could not reach Martha API: {exc.reason}") from exc
        else:
            detail = (
                last.read().decode("utf-8", errors="replace")
                if last and last.fp
                else "ingestion capacity exhausted"
            )
            raise MarthaBackpressure(429, detail, "Upload exhausted retries on 429")

        doc_id = payload.get("id") or payload.get("document_id")
        if not doc_id:
            raise MarthaAPIError(502, payload, "Document upload returned no id")
        return str(doc_id)

    def move_document(
        self,
        document_id: str,
        *,
        target_collection_id: str,
        collection_id: str | None = None,  # accepted for compat; not in the route path
        tenant_id: str | None = None,
    ) -> Any:
        # Route: POST /api/admin/documents/{document_id}/move (document_router,
        # prefix /api/admin/documents). The current collection is NOT in the
        # path — the handler re-homes by document_id + tenant. Body carries the
        # target collection.
        path = f"/admin/documents/{document_id}/move"
        return self.request(
            "POST", path, json_body={"target_collection_id": target_collection_id}, tenant_id=tenant_id
        )

    # ---- workflow execution -------------------------------------------------
    def trigger_workflow(self, workflow_name: str, user_inputs: dict[str, Any]) -> str:
        created = self.request(
            "POST",
            f"/service/workflows/{workflow_name}/execute",
            json_body={"user_inputs": user_inputs},
        )
        exec_id = (created or {}).get("execution_id") or (created or {}).get("workflow_id")
        if not exec_id:
            raise MarthaAPIError(502, created, "workflow trigger returned no execution_id")
        return str(exec_id)

    def get_execution(self, execution_id: str) -> dict[str, Any]:
        return self.request("GET", f"/service/executions/{execution_id}") or {}

    # ---- approvals ----------------------------------------------------------
    def list_pending_approvals(self, *, tenant_id: str | None = None) -> list[dict[str, Any]]:
        result = self.request(
            "GET", "/admin/approvals", params={"status": "pending"}, tenant_id=tenant_id
        )
        return result if isinstance(result, list) else []

    def resolve_approval(
        self,
        case_id: str,
        *,
        decision: str,
        comment: str | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        return self.request(
            "PUT",
            f"/admin/approvals/{case_id}/resolve",
            json_body={"decision": decision, "comment": comment},
            tenant_id=tenant_id,
        )


__all__ = [
    "Auth",
    "BearerAuth",
    "ServiceAccountAuth",
    "MarthaClient",
    "MarthaError",
]
