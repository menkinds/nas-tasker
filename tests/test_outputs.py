import os

import pytest

from nas_tasker.errors import ToolError
from nas_tasker.outputs import MAX_TAIL_BYTES, read_tail


def write_log(root, name, text):
    d = root / name
    d.mkdir(exist_ok=True)
    (d / "latest.log").write_bytes(text if isinstance(text, bytes) else text.encode())


def code_of(fn):
    with pytest.raises(ToolError) as exc:
        fn()
    return exc.value.code


def test_tail_last_lines(tmp_path):
    write_log(tmp_path, "bot-a", "".join(f"line {i}\n" for i in range(10)))
    assert read_tail(str(tmp_path), "bot-a", 3) == ("line 7\nline 8\nline 9", True)
    assert read_tail(str(tmp_path), "bot-a", 100) == (
        "\n".join(f"line {i}" for i in range(10)),
        False,
    )


def test_missing_output(tmp_path):
    assert code_of(lambda: read_tail(str(tmp_path), "bot-a", 10)) == "OUTPUT_NOT_FOUND"
    (tmp_path / "bot-a").mkdir()
    assert code_of(lambda: read_tail(str(tmp_path), "bot-a", 10)) == "OUTPUT_NOT_FOUND"


def test_symlinked_task_dir_rejected(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "latest.log").write_text("secret\n")
    root = tmp_path / "out"
    root.mkdir()
    os.symlink(outside, root / "bot-a")
    assert code_of(lambda: read_tail(str(root), "bot-a", 10)) == "NOT_ALLOWED"


def test_symlinked_log_file_rejected(tmp_path):
    (tmp_path / "passwd").write_text("root:x:0:0\n")
    (tmp_path / "bot-a").mkdir()
    os.symlink(tmp_path / "passwd", tmp_path / "bot-a" / "latest.log")
    assert code_of(lambda: read_tail(str(tmp_path), "bot-a", 10)) == "NOT_ALLOWED"


def test_fifo_does_not_hang(tmp_path):
    (tmp_path / "bot-a").mkdir()
    os.mkfifo(tmp_path / "bot-a" / "latest.log")
    assert code_of(lambda: read_tail(str(tmp_path), "bot-a", 10)) == "OUTPUT_NOT_FOUND"


def test_large_file_is_capped(tmp_path):
    write_log(tmp_path, "bot-a", "x" * 100 + "\n" + ("y" * 99 + "\n") * 5000)
    tail, truncated = read_tail(str(tmp_path), "bot-a", 1000)
    assert truncated is True
    assert len(tail.encode()) <= MAX_TAIL_BYTES
    assert "x" not in tail


def test_single_huge_line_still_returns_something(tmp_path):
    write_log(tmp_path, "bot-a", "z" * (MAX_TAIL_BYTES * 2))
    tail, truncated = read_tail(str(tmp_path), "bot-a", 10)
    assert truncated is True
    assert len(tail) == MAX_TAIL_BYTES


def test_invalid_utf8_is_replaced(tmp_path):
    write_log(tmp_path, "bot-a", b"ok \xff\xfe done\n")
    tail, _ = read_tail(str(tmp_path), "bot-a", 10)
    assert tail.startswith("ok ") and tail.endswith(" done")
