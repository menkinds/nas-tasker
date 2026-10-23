import pytest

from nas_tasker.errors import ToolError
from nas_tasker.identity import Identity
from nas_tasker.shares import MAX_PASSWORD_ATTEMPTS, ShareService

from .mock_dsm import SHARE_PASSWORD

pytestmark = pytest.mark.anyio

ME = Identity("menkinds@gmail.com", "bearer+access")
OTHER = Identity("reservoir.crabs@gmail.com", "bearer+access")


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def shares(dsm, clock):
    return ShareService(
        dsm,
        frozenset({"secret", "other"}),
        "https://tasker.example.com",
        mount_ttl_seconds=600,
        confirm_ttl_seconds=300,
        clock=clock,
    )


async def code_of(coro):
    with pytest.raises(ToolError) as exc:
        await coro
    return exc.value.code


# --- mount ---------------------------------------------------------------


async def test_mount_step1_returns_link_not_password(shares, mock_dsm):
    result = await shares.mount_share("secret")
    assert result["status"] == "pending_user_input"
    assert result["ui_url"] == f"https://tasker.example.com/ui/mount/{result['request_id']}"
    assert ("SYNO.Core.Share.Crypto", "decrypt") not in mock_dsm.calls


async def test_mount_rejects_unlisted_and_mounted(shares):
    assert await code_of(shares.mount_share("homes")) == "SHARE_NOT_ALLOWED"
    assert await code_of(shares.mount_share("other")) == "ALREADY_MOUNTED"


async def test_mount_recall_reuses_pending_request(shares):
    first = await shares.mount_share("secret")
    again = await shares.mount_share("secret")
    assert again["request_id"] == first["request_id"]


async def test_complete_mount_then_status(shares, mock_dsm):
    req_id = (await shares.mount_share("secret"))["request_id"]
    req = shares.open_request(req_id)
    assert await shares.complete_mount(req_id, req.csrf, SHARE_PASSWORD) == "mounted"
    assert mock_dsm.shares["secret"]["is_mounted"] is True
    assert await shares.mount_share("secret", req_id) == {"status": "mounted"}
    # one-time: the link cannot be used again
    assert await code_of(shares.complete_mount(req_id, req.csrf, SHARE_PASSWORD)) == "REQUEST_EXPIRED"


async def test_bad_csrf_is_forbidden(shares, mock_dsm):
    req_id = (await shares.mount_share("secret"))["request_id"]
    assert await code_of(shares.complete_mount(req_id, "forged", SHARE_PASSWORD)) == "FORBIDDEN"
    assert ("SYNO.Core.Share.Crypto", "decrypt") not in mock_dsm.calls


async def test_wrong_password_can_retry_until_limit(shares):
    req_id = (await shares.mount_share("secret"))["request_id"]
    csrf = shares.open_request(req_id).csrf
    for _ in range(MAX_PASSWORD_ATTEMPTS - 1):
        assert await code_of(shares.complete_mount(req_id, csrf, "bad")) == "WRONG_PASSWORD"
        shares.open_request(req_id)  # still pending
    assert await code_of(shares.complete_mount(req_id, csrf, "bad")) == "WRONG_PASSWORD"
    assert await shares.mount_share("secret", req_id) == {"status": "failed"}


async def test_mount_request_expires(shares, clock):
    req_id = (await shares.mount_share("secret"))["request_id"]
    csrf = shares.open_request(req_id).csrf
    clock.now += 600
    assert await shares.mount_share("secret", req_id) == {"status": "expired"}
    assert await code_of(shares.complete_mount(req_id, csrf, SHARE_PASSWORD)) == "REQUEST_EXPIRED"
    fresh = await shares.mount_share("secret")
    assert fresh["request_id"] != req_id


async def test_unknown_request_id_reads_as_expired(shares):
    assert await shares.mount_share("secret", "lost-after-restart") == {"status": "expired"}
    with pytest.raises(ToolError) as exc:
        shares.open_request("lost-after-restart")
    assert exc.value.code == "REQUEST_EXPIRED"


async def test_request_id_bound_to_share(shares):
    req_id = (await shares.mount_share("secret"))["request_id"]
    assert await code_of(shares.mount_share("other", req_id)) == "INVALID_INPUT"


async def test_dsm_down_during_mount_allows_retry(shares, mock_dsm):
    req_id = (await shares.mount_share("secret"))["request_id"]
    csrf = shares.open_request(req_id).csrf
    mock_dsm.down = True
    assert await code_of(shares.complete_mount(req_id, csrf, SHARE_PASSWORD)) == "DSM_UNREACHABLE"
    mock_dsm.down = False
    assert await shares.complete_mount(req_id, csrf, SHARE_PASSWORD) == "mounted"


async def test_password_not_in_errors(shares):
    req_id = (await shares.mount_share("secret"))["request_id"]
    csrf = shares.open_request(req_id).csrf
    with pytest.raises(ToolError) as exc:
        await shares.complete_mount(req_id, csrf, "my-typo-password")
    err = exc.value
    assert "my-typo-password" not in repr(err) + str(err) + str(err.to_dict())
    assert err.__cause__ is None and err.__suppress_context__


# --- unmount -------------------------------------------------------------


async def test_unmount_two_steps(shares, mock_dsm):
    step1 = await shares.unmount_share("other", None, ME)
    assert step1["status"] == "confirmation_required"
    assert step1["containers_using"] == []
    assert mock_dsm.shares["other"]["is_mounted"] is True
    assert await shares.unmount_share("other", step1["confirm_token"], ME) == {"status": "unmounted"}
    assert mock_dsm.shares["other"]["is_mounted"] is False


async def test_unmount_lists_containers(shares, mock_dsm):
    mock_dsm.shares["secret"]["is_mounted"] = True
    step1 = await shares.unmount_share("secret", None, ME)
    assert step1["containers_using"] == [
        {"name": "jellyfin", "state": "running", "mount_path": "/media"}
    ]


async def test_unmount_token_rules(shares, mock_dsm, clock):
    mock_dsm.shares["secret"]["is_mounted"] = True
    token = (await shares.unmount_share("other", None, ME))["confirm_token"]
    assert await code_of(shares.unmount_share("other", "made-up", ME)) == "INVALID_CONFIRM_TOKEN"
    assert await code_of(shares.unmount_share("secret", token, ME)) == "INVALID_CONFIRM_TOKEN"
    assert await code_of(shares.unmount_share("other", token, OTHER)) == "INVALID_CONFIRM_TOKEN"
    assert await shares.unmount_share("other", token, ME) == {"status": "unmounted"}
    assert await code_of(shares.unmount_share("other", token, ME)) == "INVALID_CONFIRM_TOKEN"

    mock_dsm.shares["other"]["is_mounted"] = True
    late = (await shares.unmount_share("other", None, ME))["confirm_token"]
    clock.now += 300
    assert await code_of(shares.unmount_share("other", late, ME)) == "CONFIRM_TOKEN_EXPIRED"
    assert mock_dsm.shares["other"]["is_mounted"] is True


async def test_unmount_refuses_when_not_mounted_or_lookup_fails(shares, mock_dsm):
    assert await code_of(shares.unmount_share("secret", None, ME)) == "NOT_MOUNTED"
    mock_dsm.fail_containers = True
    assert await code_of(shares.unmount_share("other", None, ME)) == "CONTAINER_LOOKUP_FAILED"
    assert await code_of(shares.unmount_share("homes", None, ME)) == "SHARE_NOT_ALLOWED"


async def test_lost_mount_reply_then_retry_reports_mounted(shares, mock_dsm):
    req_id = (await shares.mount_share("secret"))["request_id"]
    csrf = shares.open_request(req_id).csrf
    mock_dsm.lose_decrypt_reply = True
    assert await code_of(shares.complete_mount(req_id, csrf, SHARE_PASSWORD)) == "DSM_UNREACHABLE"
    mock_dsm.lose_decrypt_reply = False
    assert mock_dsm.shares["secret"]["is_mounted"] is True  # DSM did mount it
    assert await shares.complete_mount(req_id, csrf, SHARE_PASSWORD) == "mounted"
    assert mock_dsm.calls.count(("SYNO.Core.Share.Crypto", "decrypt")) == 1
    assert await shares.mount_share("secret", req_id) == {"status": "mounted"}
