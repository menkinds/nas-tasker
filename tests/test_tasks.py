import asyncio

import pytest

from nas_tasker.errors import ToolError
from nas_tasker.tasks import TaskService

pytestmark = pytest.mark.anyio


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def mono():
    return Clock()


@pytest.fixture
def service(dsm, tmp_path, mono):
    return TaskService(dsm, str(tmp_path), monotonic=mono)


async def code_of(coro):
    with pytest.raises(ToolError) as exc:
        await coro
    return exc.value.code


async def test_list_only_bot_tasks(service):
    result = await service.list_tasks()
    assert [t["name"] for t in result["tasks"]] == ["bot-gbrain-rebuild", "bot-disabled"]
    assert set(result["tasks"][0]) == {
        "name", "id", "enabled", "status", "last_run_at", "last_exit_code", "next_run_at"
    }


async def test_run_task_starts(service, mock_dsm):
    result = await service.run_task("bot-gbrain-rebuild")
    assert result["started"] is True and result["name"] == "bot-gbrain-rebuild"
    assert mock_dsm.runs == [1]


async def test_non_bot_task_cannot_run_or_be_read(service, mock_dsm):
    assert await code_of(service.run_task("nightly-backup")) == "NOT_ALLOWED"
    assert await code_of(service.task_result("nightly-backup")) == "NOT_ALLOWED"
    assert mock_dsm.runs == []


async def test_unknown_and_disabled(service):
    assert await code_of(service.run_task("bot-nope")) == "TASK_NOT_FOUND"
    assert await code_of(service.run_task("bot-disabled")) == "TASK_DISABLED"


async def test_duplicate_names_refused(service, mock_dsm):
    mock_dsm.tasks.append(dict(mock_dsm.tasks[0], id=9))
    assert await code_of(service.run_task("bot-gbrain-rebuild")) == "TASK_AMBIGUOUS"
    assert mock_dsm.runs == []


async def test_running_task_refused(service, mock_dsm):
    mock_dsm.tasks[0]["running"] = True
    assert await code_of(service.run_task("bot-gbrain-rebuild")) == "TASK_ALREADY_RUNNING"


async def test_concurrent_requests_start_once(service, mock_dsm):
    results = await asyncio.gather(
        *(service.run_task("bot-gbrain-rebuild") for _ in range(5)), return_exceptions=True
    )
    assert sum(1 for r in results if isinstance(r, dict)) == 1
    assert all(r.code == "TASK_ALREADY_RUNNING" for r in results if isinstance(r, ToolError))
    assert mock_dsm.runs == [1]


async def test_dsm_status_lag_still_blocks_second_start(service, mock_dsm, mono):
    mock_dsm.run_sets_running = False
    await service.run_task("bot-gbrain-rebuild")
    assert await code_of(service.run_task("bot-gbrain-rebuild")) == "TASK_ALREADY_RUNNING"
    mono.now += 31
    await service.run_task("bot-gbrain-rebuild")
    assert mock_dsm.runs == [1, 1]


async def test_task_result(service, tmp_path, mock_dsm):
    (tmp_path / "bot-gbrain-rebuild").mkdir()
    (tmp_path / "bot-gbrain-rebuild" / "latest.log").write_text("a\nb\nc\n")
    result = await service.task_result("bot-gbrain-rebuild", 2)
    assert result == {
        "name": "bot-gbrain-rebuild",
        "status": "succeeded",
        "exit_code": 0,
        "finished_at": "2026-10-22 03:00",
        "output_tail": "b\nc",
        "truncated": True,
    }
    mock_dsm.tasks[0]["last_exit_code"] = 2
    assert (await service.task_result("bot-gbrain-rebuild"))["status"] == "failed"
    mock_dsm.tasks[0]["running"] = True
    running = await service.task_result("bot-gbrain-rebuild")
    assert (running["status"], running["finished_at"]) == ("running", None)


async def test_task_result_bad_tail_lines(service):
    assert await code_of(service.task_result("bot-gbrain-rebuild", 5000)) == "INVALID_INPUT"


async def test_task_result_right_after_run_is_running(service, tmp_path, mock_dsm):
    mock_dsm.run_sets_running = False
    (tmp_path / "bot-gbrain-rebuild").mkdir()
    (tmp_path / "bot-gbrain-rebuild" / "latest.log").write_text("previous run ok\n")
    await service.run_task("bot-gbrain-rebuild")
    result = await service.task_result("bot-gbrain-rebuild")
    assert (result["status"], result["finished_at"]) == ("running", None)


async def test_lost_run_reply_does_not_allow_second_start(service, mock_dsm):
    mock_dsm.lose_run_reply = True
    assert await code_of(service.run_task("bot-gbrain-rebuild")) == "DSM_UNREACHABLE"
    mock_dsm.lose_run_reply = False
    mock_dsm.tasks[0]["running"] = False  # DSM still reports idle
    assert await code_of(service.run_task("bot-gbrain-rebuild")) == "TASK_ALREADY_RUNNING"
    assert mock_dsm.runs == [1]


async def test_definite_dsm_refusal_does_not_block_retry(service, mock_dsm):
    mock_dsm.refuse_run = True
    assert await code_of(service.run_task("bot-gbrain-rebuild")) == "DSM_API_ERROR"
    mock_dsm.refuse_run = False
    assert (await service.run_task("bot-gbrain-rebuild"))["started"] is True
