import os
import stat

from .errors import ToolError

LOG_FILE = "latest.log"  # <TASK_OUTPUT_DIR>/<task name>/latest.log (spec open question 4)
MAX_TAIL_BYTES = 256 * 1024


def read_tail(output_dir: str, name: str, lines: int) -> tuple[str, bool]:
    """Last `lines` lines of a task's log, read-only, never following symlinks.

    `name` must already be validated by check_task_name.
    """
    root = os.path.realpath(output_dir)
    task_dir = os.path.join(root, name)
    path = os.path.join(task_dir, LOG_FILE)
    if os.path.islink(task_dir):
        raise ToolError("NOT_ALLOWED", "symlinks are not allowed in the output folder")
    if os.path.commonpath([root, os.path.realpath(task_dir)]) != root:
        raise ToolError("NOT_ALLOWED", "path is outside the output folder")
    try:
        # O_NOFOLLOW rejects a symlinked log file, O_NONBLOCK avoids hanging on a FIFO
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        raise ToolError("OUTPUT_NOT_FOUND", "no output for this task yet") from None
    except NotADirectoryError:
        raise ToolError("OUTPUT_NOT_FOUND", "no output for this task yet") from None
    except OSError:
        if os.path.islink(path):
            raise ToolError("NOT_ALLOWED", "symlinks are not allowed in the output folder") from None
        raise ToolError("OUTPUT_NOT_FOUND", "output file cannot be read") from None
    with os.fdopen(fd, "rb") as f:
        info = os.fstat(f.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ToolError("OUTPUT_NOT_FOUND", "output is not a regular file")
        start = max(0, info.st_size - MAX_TAIL_BYTES)
        f.seek(start)
        data = f.read(MAX_TAIL_BYTES)
    all_lines = data.decode("utf-8", errors="replace").splitlines()
    if start > 0 and len(all_lines) > 1:
        all_lines = all_lines[1:]  # first line was cut by the byte cap
    truncated = start > 0 or len(all_lines) > lines
    return "\n".join(all_lines[-lines:]), truncated
