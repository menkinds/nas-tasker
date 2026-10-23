import pytest

from nas_tasker.dsm import DsmApiError, DsmClient, ForbiddenDsmCall
from nas_tasker.dsm.api import ALLOWED_METHODS
from nas_tasker.errors import ToolError

pytestmark = pytest.mark.anyio


async def test_list_tasks_parses_fields(dsm):
    tasks = await dsm.list_tasks()
    first = tasks[0]
    assert (first.id, first.name, first.enabled, first.status) == (1, "bot-gbrain-rebuild", True, "idle")
    assert first.last_exit_code == 0
    assert first.next_run_at == "2026-10-24 03:00"
    assert first.real_owner == "root"


async def test_missing_running_field_is_unknown(dsm, mock_dsm):
    del mock_dsm.tasks[0]["running"]
    assert (await dsm.list_tasks())[0].status == "unknown"


async def test_logs_in_once_and_reuses_sid(dsm, mock_dsm):
    await dsm.list_tasks()
    await dsm.list_tasks()
    assert mock_dsm.logins == 1


async def test_relogin_after_session_expiry(dsm, mock_dsm):
    await dsm.list_tasks()
    mock_dsm.expire_sessions()
    assert len(await dsm.list_tasks()) == 3
    assert mock_dsm.logins == 2


async def test_bad_dsm_credentials(mock_dsm):
    client = DsmClient("http://dsm.test", "nas-tasker", "wrong", transport=mock_dsm.transport())
    with pytest.raises(ToolError) as exc:
        await client.list_tasks()
    assert exc.value.code == "DSM_AUTH_FAILED"
    await client.aclose()


async def test_dsm_down_maps_to_unreachable(dsm, mock_dsm):
    mock_dsm.down = True
    with pytest.raises(ToolError) as exc:
        await dsm.list_tasks()
    assert exc.value.code == "DSM_UNREACHABLE"
    assert exc.value.__cause__ is None


async def test_dsm_error_code_is_carried(dsm, mock_dsm):
    with pytest.raises(DsmApiError) as exc:
        await dsm.get_share("missing")
    assert exc.value.dsm_error_code == 3300


@pytest.mark.parametrize(
    "api_name,method",
    [
        ("SYNO.Core.TaskScheduler", "create"),
        ("SYNO.Core.TaskScheduler", "set"),
        ("SYNO.Core.TaskScheduler", "delete"),
        ("SYNO.Core.TaskScheduler", "set_enable"),
        ("SYNO.Docker.Container", "start"),
        ("SYNO.Docker.Container", "stop"),
        ("SYNO.FileStation.Delete", "start"),
    ],
)
async def test_mutating_methods_are_blocked_before_sending(dsm, mock_dsm, api_name, method):
    with pytest.raises(ForbiddenDsmCall):
        await dsm.call(api_name, method, 1, {})
    assert mock_dsm.calls == []


def test_allowlist_has_no_mutating_task_or_docker_methods():
    for api_name, method in ALLOWED_METHODS:
        if api_name == "SYNO.Core.TaskScheduler":
            assert method in {"list", "run"}
        if api_name.startswith("SYNO.Docker"):
            assert method in {"list", "get"}
        assert not api_name.startswith("SYNO.FileStation")


async def test_share_mount_and_unmount(dsm, mock_dsm):
    assert (await dsm.get_share("secret")).mounted is False
    await dsm.mount_share("secret", "share-pw-Zq9!")
    assert (await dsm.get_share("secret")).mounted is True
    await dsm.unmount_share("secret")
    assert mock_dsm.shares["secret"]["is_mounted"] is False


async def test_wrong_share_password_error_code(dsm):
    with pytest.raises(DsmApiError) as exc:
        await dsm.mount_share("secret", "nope")
    assert exc.value.dsm_error_code == 3302


async def test_list_containers_using_share(dsm, mock_dsm):
    uses = await dsm.list_containers_using("secret")
    assert [(u.name, u.state, u.mount_path) for u in uses] == [("jellyfin", "running", "/media")]
    mock_dsm.containers[0]["binds"][0]["host_volume_file"] = "/volume1/secret/media"
    assert len(await dsm.list_containers_using("secret")) == 1


async def test_container_lookup_failure(dsm, mock_dsm):
    mock_dsm.fail_containers = True
    with pytest.raises(ToolError) as exc:
        await dsm.list_containers_using("secret")
    assert exc.value.code == "CONTAINER_LOOKUP_FAILED"


def test_repr_hides_credentials():
    client = DsmClient("http://dsm.test", "u", "dsm-secret-pw")
    assert "dsm-secret-pw" not in repr(client)
