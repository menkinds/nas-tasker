import json
import time
import uuid

from .audit import AuditLogger
from .auth import AccessVerifier, check_bearer
from .dispatch import CallContext, current_call, outcome_of
from .errors import ToolError
from .identity import Identity
from .ratelimit import AuthFailureLimiter

# /mcp is reached through a Cloudflare Access bypass app, so it is guarded by the
# bearer token alone. /api/ needs Access + bearer, /ui/ needs Access.
AUTH_METHODS = {"mcp": "bearer", "api": "bearer+access", "ui": "ui"}
MCP_SUBJECT = "mcp-bearer"


def classify(scope) -> str | None:
    """Which guard a path gets, or None (404). Anything ambiguous is refused, so
    no spelling of a path can be treated as /mcp here and as something else later."""
    path = scope["path"]
    raw = (scope.get("raw_path") or path.encode()).lower()
    if "%" in path or "\\" in path or "//" in path:
        return None
    if b"%2f" in raw or b"%2e" in raw or b"%5c" in raw:
        return None
    if any(segment in (".", "..") for segment in path.split("/")):
        return None
    if path == "/mcp" or path.startswith("/mcp/"):
        return "mcp"
    if path.startswith("/api/"):
        return "api"
    if path.startswith("/ui/"):
        return "ui"
    return None


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
    def __init__(
        self,
        app,
        verifier: AccessVerifier,
        bearer_token: str,
        audit: AuditLogger,
        failures: AuthFailureLimiter | None = None,
    ):
        self._app = app
        self._verifier = verifier
        self._bearer = bearer_token
        self._audit = audit
        self._failures = failures or AuthFailureLimiter()

    def _record(self, identity_email, auth_method, client_ip, tool, args, code, started):
        self._audit.record(
            request_id=uuid.uuid4().hex,
            actor_email=identity_email,
            auth_method=auth_method,
            client_ip=client_ip,
            tool=tool,
            args=args,
            result=outcome_of(code),
            error_code=code,
            duration_ms=round((time.monotonic() - started) * 1000),
            dsm_error_code=None,
        )

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        started = time.monotonic()
        kind = classify(scope)
        if kind is None:
            await _send_json(send, 404, ToolError("INVALID_INPUT", "not found").to_dict())
            return
        headers = _headers(scope)
        client = scope.get("client") or ("unknown", 0)
        client_ip = headers.get("cf-connecting-ip") or client[0]
        method = AUTH_METHODS[kind]
        email = None
        try:
            self._failures.check(client_ip)
            if kind == "mcp":
                check_bearer(headers.get("authorization"), self._bearer)
                identity = Identity(MCP_SUBJECT, method)
            else:
                email = self._verifier.verify(headers.get("cf-access-jwt-assertion"))
                if kind == "api":
                    check_bearer(headers.get("authorization"), self._bearer)
                identity = Identity(email, method, email)
        except ToolError as err:
            if err.code != "RATE_LIMITED":
                self._failures.failed(client_ip)
            self._record(email, method, client_ip, "auth", {"path": kind}, err.code, started)
            await _send_json(send, err.http_status, err.to_dict())
            return

        status = {"code": 500}

        async def send_and_capture(message):
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            await send(message)

        token = current_call.set(CallContext(identity, client_ip))
        try:
            if kind != "mcp":
                await self._app(scope, receive, send)
                return
            try:
                await self._app(scope, receive, send_and_capture)
            finally:
                http_status = status["code"]
                code = None if http_status < 400 else "INTERNAL" if http_status >= 500 else "INVALID_INPUT"
                self._record(None, method, client_ip, "mcp", {"http_status": http_status}, code, started)
        finally:
            current_call.reset(token)
