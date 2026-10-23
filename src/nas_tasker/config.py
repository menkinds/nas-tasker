import os
from collections.abc import Mapping
from dataclasses import dataclass, field

REQUIRED = (
    "DSM_USERNAME",
    "DSM_PASSWORD",
    "TASK_OUTPUT_DIR",
    "MCP_BEARER_TOKEN",
    "CF_ACCESS_TEAM_DOMAIN",
    "CF_ACCESS_AUD",
    "ALLOWED_EMAILS",
    "PUBLIC_BASE_URL",
)


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    dsm_url: str
    dsm_username: str
    dsm_password: str = field(repr=False)
    dsm_verify_tls: bool
    task_output_dir: str
    share_allowlist: frozenset[str]
    mcp_bearer_token: str = field(repr=False)
    cf_access_issuer: str
    cf_access_aud: str
    allowed_emails: frozenset[str]
    bind_host: str
    bind_port: int
    public_base_url: str
    audit_log_path: str
    confirm_token_ttl_seconds: int
    mount_request_ttl_seconds: int
    log_level: str


def _csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _bool(name: str, value: str) -> bool:
    low = value.strip().lower()
    if low in ("1", "true", "yes"):
        return True
    if low in ("0", "false", "no"):
        return False
    raise ConfigError(f"{name} must be true or false")


def _positive_int(name: str, value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise ConfigError(f"{name} must be an integer") from None
    if number <= 0:
        raise ConfigError(f"{name} must be positive")
    return number


def _get(env: Mapping[str, str], key: str, default: str) -> str:
    """An empty value (KEY= in .env) counts as unset."""
    return env.get(key, "").strip() or default


def load_settings(env: Mapping[str, str] = os.environ) -> Settings:
    missing = [key for key in REQUIRED if not env.get(key, "").strip()]
    if missing:
        raise ConfigError("missing settings: " + ", ".join(missing))
    if _get(env, "TASK_PREFIX", "bot-") != "bot-":
        raise ConfigError("TASK_PREFIX must be 'bot-'")
    if len(env["MCP_BEARER_TOKEN"]) < 32:
        raise ConfigError("MCP_BEARER_TOKEN must be at least 32 characters")

    team = env["CF_ACCESS_TEAM_DOMAIN"].strip().removeprefix("https://").rstrip("/")
    return Settings(
        dsm_url=_get(env, "DSM_URL", "http://127.0.0.1:5000").rstrip("/"),
        dsm_username=env["DSM_USERNAME"],
        dsm_password=env["DSM_PASSWORD"],
        dsm_verify_tls=_bool("DSM_VERIFY_TLS", _get(env, "DSM_VERIFY_TLS", "true")),
        task_output_dir=env["TASK_OUTPUT_DIR"],
        share_allowlist=frozenset(_csv(_get(env, "SHARE_ALLOWLIST", ""))),
        mcp_bearer_token=env["MCP_BEARER_TOKEN"],
        cf_access_issuer=f"https://{team}",
        cf_access_aud=env["CF_ACCESS_AUD"].strip(),
        allowed_emails=frozenset(e.lower() for e in _csv(env["ALLOWED_EMAILS"])),
        bind_host=_get(env, "BIND_HOST", "127.0.0.1"),
        bind_port=_positive_int("BIND_PORT", _get(env, "BIND_PORT", "8787")),
        public_base_url=env["PUBLIC_BASE_URL"].rstrip("/"),
        audit_log_path=_get(env, "AUDIT_LOG_PATH", "/data/audit/audit.jsonl"),
        confirm_token_ttl_seconds=_positive_int(
            "CONFIRM_TOKEN_TTL_SECONDS", _get(env, "CONFIRM_TOKEN_TTL_SECONDS", "300")
        ),
        mount_request_ttl_seconds=_positive_int(
            "MOUNT_REQUEST_TTL_SECONDS", _get(env, "MOUNT_REQUEST_TTL_SECONDS", "600")
        ),
        log_level=_get(env, "LOG_LEVEL", "INFO").upper(),
    )
