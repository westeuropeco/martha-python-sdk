"""MarthaClient tests — urllib mocked at the system boundary only."""

from __future__ import annotations

import io
import json
import urllib.error
from unittest.mock import patch

import pytest

from martha_sdk import (
    BearerAuth,
    MarthaAPIError,
    MarthaBackpressure,
    MarthaClient,
    MarthaConfigError,
    MarthaUnreachable,
)


class _Resp:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _http_error(code: int, body: bytes = b"{}"):
    return urllib.error.HTTPError("u", code, "err", {}, io.BytesIO(body))


def _client():
    return MarthaClient.for_user("http://martha/api", "Bearer jwt", "tenant-1")


# ---- auth ------------------------------------------------------------------
def test_bearer_auth_requires_value():
    with pytest.raises(MarthaConfigError):
        BearerAuth("")


def test_user_client_sends_auth_and_tenant():
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["auth"] = req.headers.get("Authorization")
        captured["tenant"] = req.headers.get("X-tenant-id")
        return _Resp(b'{"ok": true}')

    with patch("urllib.request.urlopen", fake_urlopen):
        _client().request("GET", "/admin/ping")
    assert captured["auth"] == "Bearer jwt"
    assert captured["tenant"] == "tenant-1"


def test_service_account_fetches_and_caches_token():
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        if req.full_url.endswith("/token"):
            calls["n"] += 1
            return _Resp(json.dumps({"access_token": "sa-tok", "expires_in": 300}).encode())
        # the actual API call echoes the Authorization it received
        return _Resp(json.dumps({"seen": req.headers.get("Authorization")}).encode())

    with patch("urllib.request.urlopen", fake_urlopen):
        c = MarthaClient.for_service_account(
            "http://martha/api",
            token_url="http://kc/token",
            client_id="cid",
            client_secret="sec",
        )
        r1 = c.request("GET", "/service/ping")
        r2 = c.request("GET", "/service/ping")
    assert r1["seen"] == "Bearer sa-tok"
    assert r2["seen"] == "Bearer sa-tok"
    assert calls["n"] == 1  # token cached, fetched once


# ---- error mapping ---------------------------------------------------------
def test_http_error_becomes_api_error():
    with patch("urllib.request.urlopen", lambda req, timeout=None: (_ for _ in ()).throw(_http_error(404, b'{"detail":"nope"}'))):
        with pytest.raises(MarthaAPIError) as ei:
            _client().request("GET", "/admin/missing")
    assert ei.value.status_code == 404
    assert ei.value.detail == "nope"


def test_url_error_becomes_unreachable():
    def boom(req, timeout=None):
        raise urllib.error.URLError("down")

    with patch("urllib.request.urlopen", boom):
        with pytest.raises(MarthaUnreachable):
            _client().request("GET", "/admin/ping")


# ---- domain calls ----------------------------------------------------------
def test_trigger_workflow_returns_execution_id():
    def fake(req, timeout=None):
        assert req.full_url.endswith("/service/workflows/wf/execute")
        body = json.loads(req.data)
        assert body == {"user_inputs": {"document_id": "d1"}}
        return _Resp(json.dumps({"execution_id": "svc_1"}).encode())

    with patch("urllib.request.urlopen", fake):
        assert _client().trigger_workflow("wf", {"document_id": "d1"}) == "svc_1"


def test_create_collection_returns_id():
    with patch("urllib.request.urlopen", lambda req, timeout=None: _Resp(b'{"id":"col-9"}')):
        assert _client().create_collection("intake") == "col-9"


def test_upload_document_happy_path():
    def fake(req, timeout=None):
        assert "multipart/form-data" in req.headers["Content-type"]
        return _Resp(b'{"id":"doc-9"}')

    with patch("urllib.request.urlopen", fake):
        doc = _client().upload_document(
            "col-1", filename="x.pdf", content=b"data", content_type="application/pdf"
        )
    assert doc == "doc-9"


def test_upload_document_429_exhausts_to_backpressure():
    def always_429(req, timeout=None):
        raise _http_error(429, b"busy")

    with patch("urllib.request.urlopen", always_429), patch("time.sleep", lambda *_: None):
        with pytest.raises(MarthaBackpressure):
            _client().upload_document(
                "col-1", filename="x.pdf", content=b"d", backoff_s=(1,)
            )


def test_resolve_approval_puts_decision():
    def fake(req, timeout=None):
        assert req.get_method() == "PUT"
        assert json.loads(req.data) == {"decision": "approved", "comment": "ok"}
        return _Resp(json.dumps({"status": "approved", "workflow_execution_id": "svc_1"}).encode())

    with patch("urllib.request.urlopen", fake):
        out = _client().resolve_approval("case-1", decision="approved", comment="ok")
    assert out["workflow_execution_id"] == "svc_1"
