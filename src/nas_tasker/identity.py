from dataclasses import dataclass


@dataclass(frozen=True)
class Identity:
    subject: str  # who to rate-limit and bind confirm tokens to
    auth_method: str  # "bearer" (/mcp), "bearer+access" (/api), "ui" (browser)
    email: str | None = None  # verified, lower-cased Access JWT email; None on /mcp
