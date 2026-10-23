import json

import pytest

from .app_helpers import MCP_HEADERS, audit_lines, bot_headers, make_client, mcp_call


@pytest.fixture
def client(tmp_path, mock_dsm):
    with make_client(tmp_path, mock_dsm) as c:
        yield c


def result_of(resp) -> dict:
    assert resp.status_code == 200
    body = resp.json()["result"]
    return json.loads(body["content"][0]["text"])


def test_exactly_five_tools(client):
    resp = client.post("/mcp/", headers={**MCP_HEADERS, **bot_headers()},
                       json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    names = sorted(t["name"] for t in resp.json()["result"]["tools"])
    assert names == ["list_tasks", "mount_share", "run_task", "task_result", "unmount_share"]


def test_list_tasks_over_mcp_is_audited_with_identity(client, tmp_path):
    data = result_of(mcp_call(client, "list_tasks", headers=bot_headers("reservoir.crabs@gmail.com")))
    assert [t["name"] for t in data["tasks"]] == ["bot-gbrain-rebuild", "bot-disabled"]
    [entry] = audit_lines(tmp_path)
    assert (entry["tool"], entry["actor_email"]) == ("list_tasks", "reservoir.crabs@gmail.com")


def test_errors_come_back_as_json(client, mock_dsm):
    data = result_of(mcp_call(client, "run_task", {"name": "nightly-backup"}))
    assert data["error"]["code"] == "NOT_ALLOWED"
    assert mock_dsm.runs == []


def test_mount_share_over_mcp_returns_link(client):
    data = result_of(mcp_call(client, "mount_share", {"name": "secret"}))
    assert data["status"] == "pending_user_input"
    assert data["ui_url"].startswith("https://tasker.example.com/ui/mount/")
    again = result_of(mcp_call(client, "mount_share",
                               {"name": "secret", "request_id": data["request_id"]}))
    assert again["status"] == "pending_user_input"


def test_mcp_without_auth(client, mock_dsm):
    resp = mcp_call(client, "list_tasks", headers={"authorization": "Bearer " + "t" * 40})
    assert resp.status_code == 401
    assert mock_dsm.calls == []
