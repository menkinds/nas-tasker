import json
import logging

from nas_tasker.audit import AuditLogger, safe_args
from nas_tasker.tokens import token_hash


def read_lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_record_appends_json_lines(tmp_path):
    path = tmp_path / "audit.jsonl"
    audit = AuditLogger(str(path))
    audit.record(tool="list_tasks", result="ok")
    audit.record(tool="run_task", result="denied", error_code="NOT_ALLOWED")
    lines = read_lines(path)
    assert [e["tool"] for e in lines] == ["list_tasks", "run_task"]
    assert lines[1]["error_code"] == "NOT_ALLOWED"
    # ISO8601 with timezone offset
    assert lines[0]["ts"][-6] in "+-" or lines[0]["ts"].endswith("Z")


def test_record_also_logs_to_stdout_logger(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="nas_tasker.audit")
    AuditLogger(str(tmp_path / "a.jsonl")).record(tool="list_tasks", result="ok")
    assert '"tool": "list_tasks"' in caplog.text


def test_safe_args_whitelists_and_hashes():
    args = {
        "name": "secret",
        "password": "hunter2",
        "confirm_token": "tok-raw",
        "request_id": "req-raw",
        "extra": "x",
    }
    out = safe_args("unmount_share", args)
    assert out == {
        "name": "secret",
        "confirm_token_hash": token_hash("tok-raw"),
        "request_id_hash": token_hash("req-raw"),
    }
    assert "hunter2" not in json.dumps(out)
    assert safe_args("run_task", "not a dict") == {}
    assert safe_args("unknown_tool", {"name": "x"}) == {}


def test_rotation_keeps_backups(tmp_path):
    path = tmp_path / "audit.jsonl"
    audit = AuditLogger(str(path), max_bytes=200, backups=2)
    for i in range(30):
        audit.record(tool="list_tasks", n=i, pad="x" * 50)
    assert path.exists()
    assert (tmp_path / "audit.jsonl.1").exists()
    assert (tmp_path / "audit.jsonl.2").exists()
    assert not (tmp_path / "audit.jsonl.3").exists()
    assert read_lines(path)[-1]["n"] == 29


def test_unwritable_audit_path_fails_at_startup(tmp_path):
    import pytest

    with pytest.raises(RuntimeError, match="audit log"):
        AuditLogger(str(tmp_path / "missing-dir" / "audit.jsonl"))


def test_app_refuses_to_start_without_writable_audit_log(tmp_path, mock_dsm):
    import pytest

    from nas_tasker.app import build_app
    from nas_tasker.config import load_settings

    from .test_config import BASE_ENV

    settings = load_settings(dict(BASE_ENV, AUDIT_LOG_PATH=str(tmp_path / "nope" / "a.jsonl")))
    with pytest.raises(RuntimeError, match="audit log"):
        build_app(settings, dsm_transport=mock_dsm.transport())
    assert mock_dsm.calls == []
