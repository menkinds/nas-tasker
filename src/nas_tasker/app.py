from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from .audit import AuditLogger
from .auth import AccessVerifier, KeyResolver
from .config import Settings
from .dispatch import Dispatcher
from .dsm import DsmClient
from .http_api import build_api_router
from .middleware import AuthMiddleware
from .ratelimit import RateLimiter
from .shares import ShareService
from .tasks import TaskService


def build_app(
    settings: Settings,
    *,
    dsm_transport: httpx.AsyncBaseTransport | None = None,
    key_resolver: KeyResolver | None = None,
):
    dsm = DsmClient(
        settings.dsm_url,
        settings.dsm_username,
        settings.dsm_password,
        verify_tls=settings.dsm_verify_tls,
        transport=dsm_transport,
    )
    audit = AuditLogger(settings.audit_log_path)
    tasks = TaskService(dsm, settings.task_output_dir)
    shares = ShareService(
        dsm,
        settings.share_allowlist,
        settings.public_base_url,
        settings.mount_request_ttl_seconds,
        settings.confirm_token_ttl_seconds,
    )
    dispatcher = Dispatcher(tasks, shares, audit, RateLimiter())

    @asynccontextmanager
    async def lifespan(app):
        yield
        await dsm.aclose()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(build_api_router(dispatcher))

    verifier = AccessVerifier(
        settings.cf_access_issuer,
        settings.cf_access_aud,
        settings.allowed_emails,
        key_resolver=key_resolver,
    )
    return AuthMiddleware(app, verifier, settings.mcp_bearer_token, audit)
