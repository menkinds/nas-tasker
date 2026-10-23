import hmac
from collections.abc import Callable
from typing import Any

import jwt

from .errors import ToolError

KeyResolver = Callable[[str], Any]  # JWT string -> public key


def jwks_resolver(issuer: str) -> KeyResolver:
    client = jwt.PyJWKClient(f"{issuer}/cdn-cgi/access/certs", cache_keys=True)
    return lambda token: client.get_signing_key_from_jwt(token).key


class AccessVerifier:
    """Verifies the Cloudflare Access JWT ourselves instead of trusting the edge."""

    def __init__(
        self,
        issuer: str,
        audience: str,
        allowed_emails: frozenset[str],
        key_resolver: KeyResolver | None = None,
    ):
        self._issuer = issuer
        self._audience = audience
        self._allowed = allowed_emails
        self._resolve = key_resolver or jwks_resolver(issuer)

    def verify(self, token: str | None) -> str:
        """Return the caller's lower-cased email or raise ToolError."""
        if not token:
            raise ToolError("UNAUTHENTICATED", "Cloudflare Access token is missing")
        try:
            claims = jwt.decode(
                token,
                self._resolve(token),
                algorithms=["RS256"],
                audience=self._audience,
                issuer=self._issuer,
                options={"require": ["exp", "iss", "aud"]},
            )
        except Exception:
            raise ToolError("UNAUTHENTICATED", "Cloudflare Access token is invalid") from None
        email = claims.get("email")
        # Service tokens carry no email, so they are refused (spec open question 6).
        if not isinstance(email, str) or email.lower() not in self._allowed:
            raise ToolError("FORBIDDEN", "this account is not allowed")
        return email.lower()


def check_bearer(header: str | None, expected: str) -> None:
    scheme, _, value = (header or "").partition(" ")
    if scheme.lower() != "bearer" or not value:
        raise ToolError("UNAUTHENTICATED", "bearer token is missing")
    if not hmac.compare_digest(value.strip().encode(), expected.encode()):
        raise ToolError("UNAUTHENTICATED", "bearer token is invalid")
