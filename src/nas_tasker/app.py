from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from mcp.server.transport_security import TransportSecuritySettings

from .audit import AuditLogger
from .auth import AccessVerifier, KeyResolver
from .config import Settings
from .dispatch import Dispatcher
from .dsm import DsmClient
from .http_api import build_api_router
from .mcp_server import build_mcp
from .middleware import AuthMiddleware
from .ratelimit import RateLimiter
from .shares import ShareService
from .tasks import TaskService
from .ui import build_ui_router


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

    mcp = build_mcp(dispatcher)
    mcp_app = mcp.streamable_http_app(
        streamable_http_path="/",
        json_response=True,
        stateless_http=True,
        # Host checks are replaced by Access JWT + bearer checks in AuthMiddleware.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    @asynccontextmanager
    async def lifespan(app):
        async with mcp.session_manager.run():
            yield
        await dsm.aclose()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(build_api_router(dispatcher))
    app.include_router(build_ui_router(shares, audit))
    app.mount("/mcp", mcp_app)

    verifier = AccessVerifier(
        settings.cf_access_issuer,
        settings.cf_access_aud,
        settings.allowed_emails,
        key_resolver=key_resolver,
    )
    return AuthMiddleware(app, verifier, settings.mcp_bearer_token, audit)
