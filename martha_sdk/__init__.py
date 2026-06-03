"""martha-python-sdk — a Python client for the Martha host API.

    from martha_sdk import MarthaClient, BearerAuth, ServiceAccountAuth

    # Forward an inbound user JWT (plugin BFF / on-behalf-of):
    client = MarthaClient.for_user(base_url, authorization, tenant_id)
    doc_id = client.upload_document(col_id, filename="x.pdf", content=b"...")

    # Service account (pipelines, workflow triggers, cron):
    client = MarthaClient.for_service_account(
        base_url, token_url=..., client_id=..., client_secret=...,
    )
    execution_id = client.trigger_workflow("my_workflow", {"document_id": doc_id})

FastAPI plugin glue (forwarded-header deps + error handler) lives in the
optional ``martha_sdk.plugin`` module — install ``martha-python-sdk[fastapi]``.
"""

from .client import Auth, BearerAuth, MarthaClient, ServiceAccountAuth
from .errors import (
    MarthaAPIError,
    MarthaBackpressure,
    MarthaConfigError,
    MarthaError,
    MarthaUnreachable,
)

__version__ = "0.1.0"

__all__ = [
    "MarthaClient",
    "Auth",
    "BearerAuth",
    "ServiceAccountAuth",
    "MarthaError",
    "MarthaAPIError",
    "MarthaBackpressure",
    "MarthaConfigError",
    "MarthaUnreachable",
    "__version__",
]
