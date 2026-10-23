import json
import uuid

from .audit import AuditLogger
from .auth import AccessVerifier, check_bearer
from .dispatch import CallContext, current_call, outcome_of
from .errors import ToolError
from .identity import Identity

BOT_PREFIXES = ("/mcp", "/api/")
UI_PREFIX = "/ui/"


def _headers(scope) -> dict[str, str]:
    return {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}


async def _send_json(send, status: int, payload: dict) -> None:
    body = json.dumps(payload).encode()
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [(b"content-type", b"application/json"), (b"cache-control", b"no-store")],
    })
    await send({"type": "http.response.body", "body": body})


class AuthMiddleware:
    """Access JWT on every path; bearer token too on bot paths. Everything else is 404."""

    def __init__(self, app, verifier: AccessVerifier, bearer_token: str, audit: AuditLogger):
        self._app = app
        self._verifier = verifier
        self._bearer = bearer_token
        self._audit = audit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        path = scope["path"]
        is_bot = path == "/mcp" or path.startswith(BOT_PREFIXES)
        if not is_bot and not path.startswith(UI_PREFIX):
            await _send_json(send, 404, ToolError("INVALID_INPUT", "not found").to_dict())
            return
        headers = _headers(scope)
        client_ip = headers.get("cf-connecting-ip")
        email = None
        try:
            email = self._verifier.verify(headers.get("cf-access-jwt-assertion"))
            if is_bot:
                check_bearer(headers.get("authorization"), self._bearer)
        except ToolError as err:
            self._audit.record(
                request_id=uuid.uuid4().hex,
                actor_email=email,
                auth_method="bearer+access" if is_bot else "ui",
                client_ip=client_ip,
                tool="auth",
                args={"path": "/mcp" if path.startswith("/mcp") else path.split("/")[1]},
                result=outcome_of(err.code),
                error_code=err.code,
                duration_ms=0,
                dsm_error_code=None,
            )
            await _send_json(send, err.http_status, err.to_dict())
            return
        identity = Identity(email, "bearer+access" if is_bot else "ui")
        token = current_call.set(CallContext(identity, client_ip))
        try:
            await self._app(scope, receive, send)
        finally:
            current_call.reset(token)
