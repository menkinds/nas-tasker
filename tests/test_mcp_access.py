"""Bot access per the 2026-10-23 bot-access instructions: /mcp is bearer-only
(Cloudflare bypasses Access for it), everything else still needs Access."""

import json

import pytest

from .app_helpers import BEARER, MCP_HEADERS, audit_lines, make_client, mcp_call
from .jwt_helpers import make_jwt

BEARER_ONLY = {"authorization": f"Bearer {BEARER}", "cf-connecting-ip": "198.51.100.1"}


@pytest.fixture
def client(tmp_path, mock_dsm):
    with make_client(tmp_path, mock_dsm) as c:
        yield c


def result_of(resp) -> dict:
    assert resp.status_code == 200, resp.text
    return json.loads(resp.json()["result"]["content"][0]["text"])


async def raw_request(app, path, raw_path=None, headers=None):
    """Send one request with an exact, un-normalised path (clients would clean it up)."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": "list_tasks", "arguments": {}}}).encode()
    all_headers = {**MCP_HEADERS, **BEARER_ONLY, "content-type": "application/json",
                   **(headers or {})}
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "POST", "scheme": "https", "root_path": "", "query_string": b"",
        "path": path, "raw_path": raw_path or path.encode(),
        "headers": [(k.encode(), v.encode()) for k, v in all_headers.items()],
        "client": ("198.51.100.1", 1234), "server": ("tasker.example.com", 443),
    }
    messages, delivered = [], False

    async def receive():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)

    await app(scope, receive, send)
    return next(m["status"] for m in messages if m["type"] == "http.response.start")


def test_mcp_needs_only_the_bearer_token(client, tmp_path):
    data = result_of(mcp_call(client, "list_tasks", headers=BEARER_ONLY))
    assert [t["name"] for t in data["tasks"]] == ["bot-gbrain-rebuild", "bot-disabled"]
    tool_entry = next(e for e in audit_lines(tmp_path) if e["tool"] == "list_tasks")
    assert (tool_entry["auth_method"], tool_entry["actor_email"]) == ("bearer", None)


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"authorization": "Bearer wrong"},
        {"authorization": "Bearer " + "g" * 40},  # GBrain's token
        {"cf-access-jwt-assertion": make_jwt()},  # Access alone is not enough on /mcp
    ],
    ids=["none", "wrong", "gbrain", "jwt-only"],
)
def test_mcp_rejects_missing_or_wrong_bearer(client, mock_dsm, headers):
    resp = mcp_call(client, "list_tasks", headers=headers)
    assert resp.status_code == 401
    assert mock_dsm.calls == []


def test_other_paths_still_need_access(client):
    assert client.post("/api/list_tasks", headers=BEARER_ONLY, json={}).status_code == 401
    assert client.get("/ui/mount/x", headers=BEARER_ONLY).status_code == 401


@pytest.mark.parametrize(
    "path,raw_path",
    [
        ("/mcp/../ui/mount/x", None),
        ("/mcp/../api/list_tasks", None),
        ("/mcp/../api/list_tasks", b"/mcp%2F..%2Fapi/list_tasks"),
        ("/mcp/%2e%2e/ui/mount/x", None),
        ("/mcp/./", None),
        ("//mcp/", None),
        ("/mcp//", None),
        ("/mcp\\..\\ui/mount/x", None),
        ("/mcpx", None),
        ("/mcp-admin/", None),
        ("/MCP/", None),
    ],
)
def test_mcp_prefix_tricks_never_skip_access(client, mock_dsm, path, raw_path):
    status = client.portal.call(raw_request, client.app, path, raw_path)
    assert status == 404
    assert mock_dsm.calls == []


def test_every_mcp_request_is_audited_without_token(client, tmp_path):
    client.post("/mcp/", headers={**MCP_HEADERS, **BEARER_ONLY},
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    mcp_call(client, "list_tasks", headers={"authorization": "Bearer wrong"})
    entries = audit_lines(tmp_path)
    assert [(e["tool"], e["result"]) for e in entries] == [("mcp", "ok"), ("auth", "denied")]
    assert entries[0]["args"] == {"http_status": 200}
    assert entries[1]["auth_method"] == "bearer"
    text = (tmp_path / "audit.jsonl").read_text()
    assert BEARER not in text and "Bearer wrong" not in text


def test_repeated_auth_failures_block_the_ip(client, mock_dsm):
    bad = {"authorization": "Bearer wrong", "cf-connecting-ip": "198.51.100.66"}
    for _ in range(10):
        assert mcp_call(client, "list_tasks", headers=bad).status_code == 401
    good_same_ip = dict(BEARER_ONLY, **{"cf-connecting-ip": "198.51.100.66"})
    blocked = mcp_call(client, "list_tasks", headers=good_same_ip)
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "RATE_LIMITED"
    assert mcp_call(client, "list_tasks", headers=BEARER_ONLY).status_code == 200
    assert mock_dsm.calls.count(("SYNO.Core.TaskScheduler", "list")) == 1


def test_mount_share_over_mcp_still_only_returns_a_link(client):
    data = result_of(mcp_call(client, "mount_share", {"name": "secret"}, headers=BEARER_ONLY))
    assert data["status"] == "pending_user_input"
    assert data["ui_url"].startswith("https://tasker.example.com/ui/mount/")
    assert "password" not in json.dumps(data)
