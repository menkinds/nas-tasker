import asyncio
import json
from dataclasses import dataclass

import httpx

from ..errors import ToolError
from . import api


class ForbiddenDsmCall(RuntimeError):
    """A code path tried to call a DSM method outside the allowlist."""


class DsmApiError(ToolError):
    def __init__(self, dsm_error_code: int | None):
        super().__init__("DSM_API_ERROR", "DSM API call failed", dsm_error_code=dsm_error_code)


@dataclass(frozen=True)
class TaskInfo:
    id: int
    name: str
    enabled: bool
    status: str  # idle | running | unknown
    last_run_at: str | None
    last_exit_code: int | None
    next_run_at: str | None
    real_owner: str | None


@dataclass(frozen=True)
class ShareInfo:
    name: str
    encrypted: bool
    mounted: bool


@dataclass(frozen=True)
class ContainerUse:
    name: str
    state: str
    mount_path: str


def _encode(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return str(value)


def _bad_response() -> ToolError:
    return ToolError("DSM_API_ERROR", "unexpected DSM response")


def _share_of(host_path: str) -> str:
    parts = host_path.strip("/").split("/")
    if len(parts) > 1 and parts[0].startswith("volume") and parts[0][6:].isdigit():
        return parts[1]
    return parts[0]


class DsmClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        verify_tls: bool = True,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 15.0,
    ):
        self._http = httpx.AsyncClient(
            base_url=base_url, verify=verify_tls, transport=transport, timeout=timeout
        )
        self._username = username
        self._password = password
        self._sid: str | None = None
        self._login_lock = asyncio.Lock()

    def __repr__(self) -> str:
        return "DsmClient()"

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _post(self, api_name: str, method: str, version: int, params: dict) -> dict:
        form = {"api": api_name, "method": method, "version": str(version)}
        form.update({k: _encode(v) for k, v in params.items()})
        try:
            resp = await self._http.post(api.ENTRY_PATH, data=form)
            resp.raise_for_status()
            body = resp.json()
        except (httpx.HTTPError, ValueError):
            # from None: never chain the original error, which may carry request details
            raise ToolError("DSM_UNREACHABLE", "DSM is unreachable") from None
        if not isinstance(body, dict):
            raise _bad_response()
        return body

    async def _login(self) -> None:
        async with self._login_lock:
            if self._sid is not None:
                return
            name, version = api.AUTH
            body = await self._post(
                name,
                "login",
                version,
                {
                    "account": self._username,
                    "passwd": self._password,
                    "session": "nas-tasker",
                    "format": "sid",
                },
            )
            if not body.get("success"):
                code = (body.get("error") or {}).get("code")
                raise ToolError("DSM_AUTH_FAILED", "DSM login failed", dsm_error_code=code)
            try:
                self._sid = str(body["data"]["sid"])
            except (KeyError, TypeError):
                raise _bad_response() from None

    async def call(
        self, api_name: str, method: str, version: int, params: dict | None = None
    ) -> dict:
        if (api_name, method) not in api.ALLOWED_METHODS:
            raise ForbiddenDsmCall(f"{api_name}.{method}")
        for attempt in (1, 2):
            if self._sid is None:
                await self._login()
            body = await self._post(api_name, method, version, {**(params or {}), "_sid": self._sid})
            if body.get("success"):
                data = body.get("data")
                return data if isinstance(data, dict) else {}
            code = (body.get("error") or {}).get("code")
            if code in api.SESSION_ERROR_CODES and attempt == 1:
                self._sid = None
                continue
            raise DsmApiError(code)
        raise AssertionError("unreachable")

    # --- task scheduler -------------------------------------------------

    async def list_tasks(self) -> list[TaskInfo]:
        name, version = api.TASK_SCHEDULER
        data = await self.call(name, "list", version, {"offset": 0, "limit": 1000})
        try:
            return [self._parse_task(raw) for raw in data["tasks"]]
        except (KeyError, TypeError, ValueError):
            raise _bad_response() from None

    @staticmethod
    def _parse_task(raw: dict) -> TaskInfo:
        running = raw.get("running")
        status = "unknown" if running is None else ("running" if running else "idle")
        exit_code = raw.get("last_exit_code")
        return TaskInfo(
            id=int(raw["id"]),
            name=str(raw["name"]),
            enabled=bool(raw.get("enable")),
            status=status,
            last_run_at=raw.get("last_run_time"),
            last_exit_code=int(exit_code) if exit_code is not None else None,
            next_run_at=raw.get("next_trigger_time"),
            real_owner=raw.get("real_owner") or raw.get("owner"),
        )

    async def run_task(self, task: TaskInfo) -> None:
        name, version = api.TASK_SCHEDULER
        await self.call(
            name, "run", version, {"tasks": [{"id": task.id, "real_owner": task.real_owner}]}
        )

    # --- shares ---------------------------------------------------------

    async def get_share(self, share: str) -> ShareInfo:
        name, version = api.SHARE
        data = await self.call(name, "get", version, {"name": share})
        try:
            raw = data["shares"][0]
            return ShareInfo(
                name=str(raw["name"]),
                encrypted=bool(raw["encryption"]),
                mounted=bool(raw["is_mounted"]),
            )
        except (KeyError, IndexError, TypeError):
            raise _bad_response() from None

    async def mount_share(self, share: str, password: str) -> None:
        name, version = api.SHARE_CRYPTO
        await self.call(name, "decrypt", version, {"name": share, "password": password})

    async def unmount_share(self, share: str) -> None:
        name, version = api.SHARE_CRYPTO
        await self.call(name, "encrypt", version, {"name": share})

    # --- containers (read only) ------------------------------------------

    async def list_containers_using(self, share: str) -> list[ContainerUse]:
        name, version = api.DOCKER_CONTAINER
        try:
            listing = await self.call(name, "list", version, {"limit": -1, "offset": 0})
            found = []
            for item in listing["containers"]:
                detail = await self.call(name, "get", version, {"name": item["name"]})
                for binding in detail["profile"].get("volume_bindings", []):
                    if _share_of(binding["host_volume_file"]) == share:
                        found.append(
                            ContainerUse(
                                name=str(item["name"]),
                                state=str(item.get("status", "unknown")),
                                mount_path=str(binding["mount_point"]),
                            )
                        )
            return found
        except (ToolError, KeyError, TypeError, AttributeError):
            raise ToolError(
                "CONTAINER_LOOKUP_FAILED", "could not check which containers use the share"
            ) from None
