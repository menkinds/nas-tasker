import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SYNOLOGY = ROOT / "synology"

SPEC_ENV_KEYS = [
    "DSM_URL", "DSM_USERNAME", "DSM_PASSWORD", "DSM_VERIFY_TLS", "TASK_PREFIX",
    "TASK_OUTPUT_DIR", "SHARE_ALLOWLIST", "MCP_BEARER_TOKEN", "CF_ACCESS_TEAM_DOMAIN",
    "CF_ACCESS_AUD", "ALLOWED_EMAILS", "CLOUDFLARE_TUNNEL_TOKEN", "BIND_HOST", "BIND_PORT",
    "PUBLIC_BASE_URL", "AUDIT_LOG_PATH", "CONFIRM_TOKEN_TTL_SECONDS",
    "MOUNT_REQUEST_TTL_SECONDS", "LOG_LEVEL", "TZ",
]


def shipped_files():
    yield ROOT / "Dockerfile"
    yield from SYNOLOGY.iterdir()
    yield from (ROOT / "src").rglob("*.py")


def test_no_chmod_or_chown_anywhere():
    for path in shipped_files():
        text = path.read_text()
        assert not re.search(r"\bch(mod|own)\b|--chown|--chmod", text), path


def test_synology_folder_has_only_deploy_files():
    assert sorted(p.name for p in SYNOLOGY.iterdir()) == [".env.example", "docker-compose.yml"]


def test_compose_hardening():
    text = (SYNOLOGY / "docker-compose.yml").read_text()
    assert "docker.sock" not in text
    assert "privileged" not in text
    assert text.count("network_mode: host") == 2
    assert ":/data/output:ro" in text
    volume_lines = [l for l in text.splitlines() if l.strip().startswith("- ${")]
    assert len(volume_lines) == 2
    assert "no-new-privileges:true" in text and "read_only: true" in text


def test_env_example_lists_every_key_without_values():
    lines = [l for l in (SYNOLOGY / ".env.example").read_text().splitlines()
             if l and not l.startswith("#")]
    keys = [l.split("=", 1)[0] for l in lines]
    assert all(l.endswith("=") for l in lines)
    for key in SPEC_ENV_KEYS:
        assert key in keys, key


def test_server_binds_localhost_by_default():
    from nas_tasker.config import load_settings
    from .test_config import BASE_ENV

    assert load_settings(BASE_ENV).bind_host == "127.0.0.1"
