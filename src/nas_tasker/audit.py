import json
import logging
import os
import threading
from datetime import datetime

from .tokens import token_hash

# Only these argument fields are ever written. Capability-like values are hashed.
SAFE_ARGS = {
    "list_tasks": (),
    "run_task": ("name",),
    "task_result": ("name", "tail_lines"),
    "mount_share": ("name",),
    "unmount_share": ("name",),
    "ui_mount": ("name",),
}
HASHED_ARGS = ("request_id", "confirm_token")

MAX_BYTES = 10 * 1024 * 1024
BACKUPS = 5

log = logging.getLogger("nas_tasker.audit")


def safe_args(tool: str, args: object) -> dict:
    if not isinstance(args, dict):
        return {}
    out = {k: args[k] for k in SAFE_ARGS.get(tool, ()) if k in args}
    for key in HASHED_ARGS:
        value = args.get(key)
        if isinstance(value, str):
            out[f"{key}_hash"] = token_hash(value)
    return out


class AuditLogger:
    """Append-only JSON Lines audit log with size-based rotation."""

    def __init__(self, path: str, max_bytes: int = MAX_BYTES, backups: int = BACKUPS):
        self._path = path
        self._max_bytes = max_bytes
        self._backups = backups
        self._lock = threading.Lock()

    def record(self, **fields) -> None:
        entry = {"ts": datetime.now().astimezone().isoformat(timespec="milliseconds"), **fields}
        line = json.dumps(entry, ensure_ascii=False, sort_keys=True, default=str)
        with self._lock:
            self._rotate_if_needed()
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        log.info(line)

    def _rotate_if_needed(self) -> None:
        try:
            if os.path.getsize(self._path) < self._max_bytes:
                return
        except FileNotFoundError:
            return
        for i in range(self._backups - 1, 0, -1):
            src = f"{self._path}.{i}"
            if os.path.exists(src):
                os.replace(src, f"{self._path}.{i + 1}")
        os.replace(self._path, f"{self._path}.1")
