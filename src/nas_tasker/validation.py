import re

from .errors import ToolError

TASK_PREFIX = "bot-"
_TASK_NAME = re.compile(r"bot-[a-z0-9-]{1,64}")
DEFAULT_TAIL_LINES = 100
MAX_TAIL_LINES = 1000


def check_task_name(name: object) -> str:
    if not isinstance(name, str):
        raise ToolError("INVALID_INPUT", "name must be a string")
    if not name.startswith(TASK_PREFIX):
        raise ToolError("NOT_ALLOWED", "only bot- tasks are allowed")
    if not _TASK_NAME.fullmatch(name):
        raise ToolError("INVALID_INPUT", "invalid task name")
    return name


def check_share_name(name: object, allowlist: frozenset[str]) -> str:
    if not isinstance(name, str):
        raise ToolError("INVALID_INPUT", "name must be a string")
    if name not in allowlist:
        raise ToolError("SHARE_NOT_ALLOWED", "share is not in the allowlist")
    return name


def check_tail_lines(value: object) -> int:
    if value is None:
        return DEFAULT_TAIL_LINES
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolError("INVALID_INPUT", "tail_lines must be an integer")
    if not 1 <= value <= MAX_TAIL_LINES:
        raise ToolError("INVALID_INPUT", f"tail_lines must be between 1 and {MAX_TAIL_LINES}")
    return value
