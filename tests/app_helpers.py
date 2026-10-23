import json

from starlette.testclient import TestClient

from nas_tasker.app import build_app
from nas_tasker.config import load_settings

from .jwt_helpers import make_jwt, resolver
from .mock_dsm import DSM_PASS, DSM_USER

BEARER = "t" * 40
MCP_HEADERS = {"accept": "application/json, text/event-stream"}


def make_client(tmp_path, mock_dsm) -> TestClient:
    out = tmp_path / "output"
    out.mkdir(exist_ok=True)
    settings = load_settings({
        "DSM_URL": "http://dsm.test",
        "DSM_USERNAME": DSM_USER,
        "DSM_PASSWORD": DSM_PASS,
        "TASK_OUTPUT_DIR": str(out),
        "SHARE_ALLOWLIST": "secret,other",
        "MCP_BEARER_TOKEN": BEARER,
        "CF_ACCESS_TEAM_DOMAIN": "myteam.cloudflareaccess.com",
        "CF_ACCESS_AUD": "aud-123",
        "ALLOWED_EMAILS": "menkinds@gmail.com,reservoir.crabs@gmail.com",
        "PUBLIC_BASE_URL": "https://tasker.example.com",
        "AUDIT_LOG_PATH": str(tmp_path / "audit.jsonl"),
    })
    app = build_app(settings, dsm_transport=mock_dsm.transport(), key_resolver=resolver)
    return TestClient(app, base_url="https://tasker.example.com")


def bot_headers(email="menkinds@gmail.com", bearer=BEARER) -> dict:
    return {"cf-access-jwt-assertion": make_jwt(email), "authorization": f"Bearer {bearer}",
            "cf-connecting-ip": "203.0.113.7"}


def ui_headers(email="menkinds@gmail.com") -> dict:
    return {"cf-access-jwt-assertion": make_jwt(email)}


def audit_lines(tmp_path) -> list[dict]:
    path = tmp_path / "audit.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def mcp_call(client, tool, arguments=None, headers=None):
    resp = client.post(
        "/mcp/",
        headers={**MCP_HEADERS, **(headers or bot_headers())},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
              "params": {"name": tool, "arguments": arguments or {}}},
    )
    return resp
