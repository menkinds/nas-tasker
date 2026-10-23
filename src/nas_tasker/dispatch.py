import logging
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass

from .audit import AuditLogger, safe_args
from .errors import DENIED_CODES, ToolError
from .identity import Identity
from .ratelimit import RateLimiter
from .shares import ShareService
from .tasks import TaskService

log = logging.getLogger("nas_tasker")

# tool -> (required keys, optional keys)
TOOL_ARGS = {
    "list_tasks": ((), ()),
    "run_task": (("name",), ()),
    "task_result": (("name",), ("tail_lines",)),
    "mount_share": (("name",), ("request_id",)),
    "unmount_share": (("name",), ("confirm_token",)),
}


@dataclass(frozen=True)
class CallContext:
    identity: Identity
    client_ip: str | None


# Set by AuthMiddleware for each authenticated request; read by HTTP and MCP handlers.
current_call: ContextVar[CallContext | None] = ContextVar("current_call", default=None)


def outcome_of(code: str | None) -> str:
    if code is None:
        return "ok"
    return "denied" if code in DENIED_CODES else "error"


def _check_args(tool: str, args: object) -> dict:
    if tool not in TOOL_ARGS:
        raise ToolError("INVALID_INPUT", "unknown tool")
    if not isinstance(args, dict):
        raise ToolError("INVALID_INPUT", "arguments must be a JSON object")
    required, optional = TOOL_ARGS[tool]
    unknown = set(args) - set(required) - set(optional)
    if unknown:
        raise ToolError("INVALID_INPUT", "unknown argument(s): " + ", ".join(sorted(unknown)))
    missing = [k for k in required if k not in args]
    if missing:
        raise ToolError("INVALID_INPUT", "missing argument(s): " + ", ".join(missing))
    return args


class Dispatcher:
    """Single entry point for the five tools, shared by the HTTP API and MCP."""

    def __init__(
        self,
        tasks: TaskService,
        shares: ShareService,
        audit: AuditLogger,
        limiter: RateLimiter,
    ):
        self._tasks = tasks
        self._shares = shares
        self._audit = audit
        self._limiter = limiter

    async def call(self, tool: str, args: object, ctx: CallContext) -> tuple[int, dict]:
        started = time.monotonic()
        code = dsm_code = None
        try:
            checked = _check_args(tool, args)
            self._limiter.check(ctx.identity.subject, tool)
            status, payload = 200, await self._invoke(tool, checked, ctx.identity)
        except ToolError as err:
            code, dsm_code = err.code, err.dsm_error_code
            status, payload = err.http_status, err.to_dict()
        except Exception as err:  # never echo internals to the caller
            log.error("internal error in %s: %s", tool, type(err).__name__)
            code = "INTERNAL"
            status, payload = 500, ToolError("INTERNAL", "internal error").to_dict()
        self._audit.record(
            request_id=uuid.uuid4().hex,
            actor_email=ctx.identity.email,
            auth_method=ctx.identity.auth_method,
            client_ip=ctx.client_ip,
            tool=tool if tool in TOOL_ARGS else "unknown",
            args=safe_args(tool, args),
            result=outcome_of(code),
            error_code=code,
            duration_ms=round((time.monotonic() - started) * 1000),
            dsm_error_code=dsm_code,
        )
        return status, payload

    async def _invoke(self, tool: str, args: dict, identity: Identity) -> dict:
        if tool == "list_tasks":
            return await self._tasks.list_tasks()
        if tool == "run_task":
            return await self._tasks.run_task(args["name"])
        if tool == "task_result":
            return await self._tasks.task_result(args["name"], args.get("tail_lines"))
        if tool == "mount_share":
            return await self._shares.mount_share(args["name"], args.get("request_id"))
        return await self._shares.unmount_share(args["name"], args.get("confirm_token"), identity)
