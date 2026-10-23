import pytest

from .app_helpers import BEARER, audit_lines, bot_headers, make_client
from .jwt_helpers import OTHER_KEY, make_jwt


@pytest.fixture
def client(tmp_path, mock_dsm):
    with make_client(tmp_path, mock_dsm) as c:
        yield c


def test_list_tasks_over_http(client, tmp_path):
    resp = client.post("/api/list_tasks", headers=bot_headers(), json={})
    assert resp.status_code == 200
    assert [t["name"] for t in resp.json()["tasks"]] == ["bot-gbrain-rebuild", "bot-disabled"]
    [entry] = audit_lines(tmp_path)
    assert entry["tool"] == "list_tasks" and entry["result"] == "ok"
    assert entry["actor_email"] == "menkinds@gmail.com"
    assert entry["auth_method"] == "bearer+access"
    assert entry["client_ip"] == "203.0.113.7"
    assert set(entry) >= {"ts", "request_id", "args", "error_code", "duration_ms", "dsm_error_code"}


def test_run_task_and_error_shape(client, mock_dsm):
    ok = client.post("/api/run_task", headers=bot_headers(), json={"name": "bot-gbrain-rebuild"})
    assert ok.status_code == 200 and ok.json()["started"] is True
    denied = client.post("/api/run_task", headers=bot_headers(), json={"name": "nightly-backup"})
    assert denied.status_code == 403
    assert denied.json() == {"error": {"code": "NOT_ALLOWED", "message": "only bot- tasks are allowed"}}
    assert mock_dsm.runs == [1]


@pytest.mark.parametrize(
    "headers,status,code",
    [
        ({}, 401, "UNAUTHENTICATED"),
        ({"authorization": f"Bearer {BEARER}"}, 401, "UNAUTHENTICATED"),
        ({"cf-access-jwt-assertion": make_jwt()}, 401, "UNAUTHENTICATED"),
        ({"cf-access-jwt-assertion": make_jwt(), "authorization": "Bearer " + "g" * 40}, 401, "UNAUTHENTICATED"),
        ({"cf-access-jwt-assertion": make_jwt(key=OTHER_KEY), "authorization": f"Bearer {BEARER}"}, 401, "UNAUTHENTICATED"),
        ({"cf-access-jwt-assertion": make_jwt("x@evil.com"), "authorization": f"Bearer {BEARER}"}, 403, "FORBIDDEN"),
    ],
    ids=["nothing", "bearer-only", "jwt-only", "gbrain-token", "bad-jwt", "stranger"],
)
def test_api_paths_need_jwt_and_bearer(client, tmp_path, mock_dsm, headers, status, code):
    # /mcp is bearer-only (see test_mcp_access.py); /api/ keeps Access + bearer.
    resp = client.post("/api/list_tasks", headers=headers, json={})
    assert resp.status_code == status
    assert resp.json()["error"]["code"] == code
    assert mock_dsm.calls == []
    [entry] = audit_lines(tmp_path)
    assert (entry["tool"], entry["result"]) == ("auth", "denied")


def test_unknown_paths_are_404_without_auth(client):
    for path in ("/", "/docs", "/openapi.json", "/webapi/entry.cgi"):
        assert client.get(path).status_code == 404


def test_bad_arguments(client, tmp_path):
    h = bot_headers()
    assert client.post("/api/run_task", headers=h, json={}).json()["error"]["code"] == "INVALID_INPUT"
    assert client.post("/api/run_task", headers=h, json={"name": "bot-a", "x": 1}).status_code == 400
    assert client.post("/api/run_task", headers=h, content=b"{not json").status_code == 400
    assert client.post("/api/run_task", headers=h, json=["bot-a"]).status_code == 400
    assert client.post("/api/delete_task", headers=h, json={}).status_code == 400
    assert {e["tool"] for e in audit_lines(tmp_path)} == {"run_task", "unknown"}


def test_rate_limit_run_task(client):
    h = bot_headers()
    codes = [
        client.post("/api/run_task", headers=h, json={"name": "bot-nope"}).json()["error"]["code"]
        for _ in range(11)
    ]
    assert codes[:10] == ["TASK_NOT_FOUND"] * 10
    assert codes[10] == "RATE_LIMITED"


def test_dsm_down_is_clean_502(client, mock_dsm):
    mock_dsm.down = True
    resp = client.post("/api/list_tasks", headers=bot_headers(), json={})
    assert resp.status_code == 502
    assert resp.json() == {"error": {"code": "DSM_UNREACHABLE", "message": "DSM is unreachable"}}


def test_unmount_confirm_token_not_logged(client, tmp_path):
    step1 = client.post("/api/unmount_share", headers=bot_headers(), json={"name": "other"}).json()
    token = step1["confirm_token"]
    done = client.post("/api/unmount_share", headers=bot_headers(),
                       json={"name": "other", "confirm_token": token})
    assert done.json() == {"status": "unmounted"}
    text = (tmp_path / "audit.jsonl").read_text()
    assert token not in text and "confirm_token_hash" in text


def test_unmount_token_bound_to_caller(client):
    token = client.post("/api/unmount_share", headers=bot_headers(),
                        json={"name": "other"}).json()["confirm_token"]
    other = client.post("/api/unmount_share", headers=bot_headers("reservoir.crabs@gmail.com"),
                        json={"name": "other", "confirm_token": token})
    assert other.json()["error"]["code"] == "INVALID_CONFIRM_TOKEN"
