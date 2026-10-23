import time
from collections import defaultdict, deque
from collections.abc import Callable

from .errors import ToolError

# tool -> (max calls, window seconds), per caller
DEFAULT_LIMITS = {
    "run_task": (10, 60.0),
    "mount_share": (10, 600.0),
    "unmount_share": (10, 600.0),
    "*": (60, 60.0),
}


class AuthFailureLimiter:
    """Blocks a client IP for a while after repeated authentication failures."""

    def __init__(
        self,
        max_failures: int = 10,
        window: float = 300.0,
        block: float = 600.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._max = max_failures
        self._window = window
        self._block = block
        self._clock = clock
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._blocked_until: dict[str, float] = {}

    def check(self, ip: str) -> None:
        until = self._blocked_until.get(ip)
        if until is not None:
            if self._clock() < until:
                raise ToolError("RATE_LIMITED", "too many failed attempts, try again later")
            del self._blocked_until[ip]

    def failed(self, ip: str) -> None:
        now = self._clock()
        hits = self._failures[ip]
        while hits and hits[0] <= now - self._window:
            hits.popleft()
        hits.append(now)
        if len(hits) >= self._max:
            self._blocked_until[ip] = now + self._block
            hits.clear()


class RateLimiter:
    def __init__(
        self,
        limits: dict[str, tuple[int, float]] = DEFAULT_LIMITS,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._limits = limits
        self._clock = clock
        self._hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)

    def check(self, caller: str, tool: str) -> None:
        limit, window = self._limits.get(tool, self._limits["*"])
        now = self._clock()
        hits = self._hits[(caller, tool)]
        while hits and hits[0] <= now - window:
            hits.popleft()
        if len(hits) >= limit:
            raise ToolError("RATE_LIMITED", "too many requests, try again later")
        hits.append(now)
