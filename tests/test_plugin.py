"""FastAPI plugin glue tests — forwarded-header deps + error handler."""

from __future__ import annotations

import base64
import json

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from martha_sdk.client import MarthaClient
from martha_sdk.errors import MarthaAPIError
from martha_sdk.plugin import (
    forwarded_authorization,
    forwarded_subject,
    install_martha_error_handler,
    tenant_id_header,
)


def _jwt(sub: str) -> str:
    seg = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{seg({'alg':'none'})}.{seg({'sub': sub})}.sig"


def _app() -> FastAPI:
    app = FastAPI()
    install_martha_error_handler(app)

    @app.get("/whoami")
    def whoami(
        tenant: str = Depends(tenant_id_header),
        sub: str = Depends(forwarded_subject),
    ):
        return {"tenant": tenant, "sub": sub}

    @app.get("/boom")
    def boom(_: str = Depends(forwarded_authorization)):
        raise MarthaAPIError(409, "conflict")

    return app


def test_deps_extract_tenant_and_subject():
    c = TestClient(_app())
    r = c.get("/whoami", headers={"X-Tenant-Id": "t1", "Authorization": f"Bearer {_jwt('emp-7')}"})
    assert r.status_code == 200
    assert r.json() == {"tenant": "t1", "sub": "emp-7"}


def test_missing_tenant_is_400():
    c = TestClient(_app())
    r = c.get("/whoami", headers={"Authorization": f"Bearer {_jwt('emp-7')}"})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "tenant_required"


def test_missing_auth_is_401():
    c = TestClient(_app())
    r = c.get("/whoami", headers={"X-Tenant-Id": "t1"})
    assert r.status_code == 401


def test_error_handler_maps_martha_api_error():
    c = TestClient(_app(), raise_server_exceptions=False)
    r = c.get("/boom", headers={"Authorization": f"Bearer {_jwt('x')}"})
    assert r.status_code == 409
    body = r.json()["error"]
    assert body["code"] == "martha_upstream_error"
    assert "message_pt" in body and "message_en" in body


def test_for_user_client_constructs():
    client = MarthaClient.for_user("http://m/api", "Bearer j", "t1")
    assert client.tenant_id == "t1"
    assert client.base_url == "http://m/api"
