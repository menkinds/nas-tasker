import hashlib
import secrets
import time
from collections.abc import Callable
from typing import Any


class TokenInvalid(Exception):
    pass


class TokenExpired(Exception):
    pass


def new_token() -> str:
    return secrets.token_urlsafe(32)  # 256 bits


def token_hash(token: str) -> str:
    """Short, non-reversible label for logs."""
    return hashlib.sha256(token.encode()).hexdigest()[:12]


class OneTimeStore:
    """In-memory one-time tokens with a TTL. Lost on restart, by design."""

    def __init__(self, ttl_seconds: int, clock: Callable[[], float] = time.time):
        self._ttl = ttl_seconds
        self._clock = clock
        self._items: dict[str, tuple[float, Any]] = {}

    def issue(self, payload: Any) -> tuple[str, float]:
        now = self._clock()
        self._items = {t: item for t, item in self._items.items() if item[0] > now}
        token = new_token()
        expires_at = now + self._ttl
        self._items[token] = (expires_at, payload)
        return token, expires_at

    def take(self, token: object, matches: Callable[[Any], bool]) -> Any:
        """Consume a token. A token whose payload does not match stays unused."""
        item = self._items.get(token) if isinstance(token, str) else None
        if item is None:
            raise TokenInvalid()
        expires_at, payload = item
        if self._clock() >= expires_at:
            del self._items[token]
            raise TokenExpired()
        if not matches(payload):
            raise TokenInvalid()
        del self._items[token]
        return payload
