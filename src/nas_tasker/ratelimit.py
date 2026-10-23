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
