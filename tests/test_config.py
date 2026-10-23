import pytest

from nas_tasker.config import ConfigError, load_settings
from nas_tasker.errors import ToolError

BASE_ENV = {
    "DSM_USERNAME": "nas-tasker",
    "DSM_PASSWORD": "dsm-secret-pw",
    "TASK_OUTPUT_DIR": "/data/output",
    "MCP_BEARER_TOKEN": "b" * 40,
    "CF_ACCESS_TEAM_DOMAIN": "myteam.cloudflareaccess.com",
    "CF_ACCESS_AUD": "aud-123",
    "ALLOWED_EMAILS": "Menkinds@gmail.com, reservoir.crabs@gmail.com",
    "PUBLIC_BASE_URL": "https://tasker.example.com/",
}


def test_defaults_and_normalisation():
    s = load_settings(BASE_ENV)
    assert s.dsm_url == "http://127.0.0.1:5000"
    assert s.dsm_verify_tls is True
    assert s.cf_access_issuer == "https://myteam.cloudflareaccess.com"
    assert s.allowed_emails == {"menkinds@gmail.com", "reservoir.crabs@gmail.com"}
    assert s.share_allowlist == frozenset()
    assert s.public_base_url == "https://tasker.example.com"
    assert (s.bind_host, s.bind_port) == ("127.0.0.1", 8787)
    assert (s.confirm_token_ttl_seconds, s.mount_request_ttl_seconds) == (300, 600)


def test_secrets_not_in_repr():
    text = repr(load_settings(BASE_ENV))
    assert "dsm-secret-pw" not in text
    assert "b" * 40 not in text


def test_missing_required_key():
    env = dict(BASE_ENV, DSM_PASSWORD="")
    with pytest.raises(ConfigError, match="DSM_PASSWORD"):
        load_settings(env)


@pytest.mark.parametrize("prefix", ["", "job-", "bot"])
def test_task_prefix_cannot_change(prefix):
    with pytest.raises(ConfigError, match="TASK_PREFIX"):
        load_settings(dict(BASE_ENV, TASK_PREFIX=prefix))


def test_short_bearer_token_rejected():
    with pytest.raises(ConfigError, match="MCP_BEARER_TOKEN"):
        load_settings(dict(BASE_ENV, MCP_BEARER_TOKEN="short"))


def test_bad_bool_and_int():
    with pytest.raises(ConfigError, match="DSM_VERIFY_TLS"):
        load_settings(dict(BASE_ENV, DSM_VERIFY_TLS="maybe"))
    with pytest.raises(ConfigError, match="BIND_PORT"):
        load_settings(dict(BASE_ENV, BIND_PORT="0"))


def test_tool_error_shape():
    err = ToolError("DSM_API_ERROR", "DSM API call failed", dsm_error_code=117)
    assert str(err) == "DSM_API_ERROR"
    assert err.http_status == 502
    assert err.to_dict() == {
        "error": {"code": "DSM_API_ERROR", "message": "DSM API call failed", "dsm_error_code": 117}
    }
