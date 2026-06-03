# martha-python-sdk

A small Python client for the [Martha](https://github.com/westeuropeco/martha)
host API — collections, documents, workflow execution, approvals. The
**TypeScript** counterpart `@westeuropeco/martha-sdk` covers the plugin *UI*
contract; this is the **Python** client for the *back-end* (plugin BFFs,
pipelines, scripts, tests).

Zero runtime dependencies (stdlib `urllib`). FastAPI is only needed for the
optional plugin glue.

## Install

```bash
# Public repo → no registry, no auth, pinned by tag:
pip install "martha-python-sdk @ git+https://github.com/westeuropeco/martha-python-sdk@v0.1.0"
# BFFs that need the FastAPI helpers:
pip install "martha-python-sdk[fastapi] @ git+https://github.com/westeuropeco/martha-python-sdk@v0.1.0"
```

## Use

```python
from martha_sdk import MarthaClient

# On-behalf-of a user (forward an inbound JWT — the plugin BFF pattern):
client = MarthaClient.for_user("https://martha/api", authorization, tenant_id)
col = client.create_collection("intake")
doc = client.upload_document(col, filename="x.pdf", content=pdf_bytes,
                             content_type="application/pdf")

# As a service account (workflow triggers, pipelines, cron):
svc = MarthaClient.for_service_account(
    "https://martha/api",
    token_url="https://kc/realms/frank/protocol/openid-connect/token",
    client_id="...", client_secret="...",
)
execution_id = svc.trigger_workflow("my_workflow", {"document_id": doc})
status = svc.get_execution(execution_id)
```

The client raises `martha_sdk.errors` (`MarthaAPIError`, `MarthaUnreachable`,
`MarthaBackpressure`, …) — never `fastapi.HTTPException` — so it works outside a
web framework.

## FastAPI plugin glue (optional)

```python
from martha_sdk.plugin import (
    tenant_id_header, forwarded_authorization, forwarded_subject,
    install_martha_error_handler,
)

install_martha_error_handler(app)  # maps MarthaError → PT/EN HTTP envelope
```

These dependencies read the host plugin-proxy's forwarded `Authorization` +
`X-Tenant-Id` headers, and `forwarded_subject` extracts the caller's `sub` from
the (already-host-validated) JWT.

## What's in scope

| | This SDK (Python) | `@westeuropeco/martha-sdk` (TS) |
|---|---|---|
| Layer | BFF / service ↔ Martha API | plugin UI ↔ admin host |
| Surface | API client, FastAPI deps | `ctx.api`, `defineRoutes`, locales |

## Releasing

```bash
# bump version in pyproject.toml + martha_sdk/__init__.py, then:
git tag v0.1.1 && git push origin --tags
# consumers bump the @vX.Y.Z in their requirement
```
