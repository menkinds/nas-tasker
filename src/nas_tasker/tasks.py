import asyncio
import time
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime

from .dsm import DsmApiError, DsmClient, TaskInfo
from .errors import ToolError
from .outputs import read_tail
from .validation import TASK_PREFIX, check_tail_lines, check_task_name

# DSM may report "idle" for a moment after "run". Refuse a second start inside this window.
RECENT_START_GRACE_SECONDS = 30.0


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def _task_dict(task: TaskInfo) -> dict:
    return {
        "name": task.name,
        "id": task.id,
        "enabled": task.enabled,
        "status": task.status,
        "last_run_at": task.last_run_at,
        "last_exit_code": task.last_exit_code,
        "next_run_at": task.next_run_at,
    }


class TaskService:
    def __init__(
        self,
        dsm: DsmClient,
        output_dir: str,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self._dsm = dsm
        self._output_dir = output_dir
        self._clock = clock
        self._monotonic = monotonic
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._started: dict[str, float] = {}

    async def _find(self, name: str) -> TaskInfo:
        matches = [t for t in await self._dsm.list_tasks() if t.name == name]
        if not matches:
            raise ToolError("TASK_NOT_FOUND", "no such bot- task")
        if len(matches) > 1:
            raise ToolError("TASK_AMBIGUOUS", "more than one task has this name")
        return matches[0]

    def _recently_started(self, name: str) -> bool:
        started = self._started.get(name)
        return started is not None and self._monotonic() - started < RECENT_START_GRACE_SECONDS

    async def list_tasks(self) -> dict:
        tasks = await self._dsm.list_tasks()
        return {"tasks": [_task_dict(t) for t in tasks if t.name.startswith(TASK_PREFIX)]}

    async def run_task(self, name: object) -> dict:
        name = check_task_name(name)
        async with self._locks[name]:
            task = await self._find(name)
            if not task.enabled:
                raise ToolError("TASK_DISABLED", "task is disabled")
            if task.status == "running" or self._recently_started(name):
                raise ToolError("TASK_ALREADY_RUNNING", "task is already running")
            # Record before calling: if DSM's reply is lost, the task may still have started.
            self._started[name] = self._monotonic()
            try:
                await self._dsm.run_task(task)
            except DsmApiError:
                del self._started[name]  # DSM definitely refused
                raise
            return {"name": name, "started": True, "started_at": iso(self._clock())}

    async def task_result(self, name: object, tail_lines: object = None) -> dict:
        name = check_task_name(name)
        lines = check_tail_lines(tail_lines)
        task = await self._find(name)
        tail, truncated = read_tail(self._output_dir, name, lines)
        if task.status == "running" or self._recently_started(name):
            status = "running"
        elif task.last_exit_code is None:
            status = "unknown"
        else:
            status = "succeeded" if task.last_exit_code == 0 else "failed"
        return {
            "name": name,
            "status": status,
            "exit_code": task.last_exit_code,
            "finished_at": None if status == "running" else task.last_run_at,
            "output_tail": tail,
            "truncated": truncated,
        }
