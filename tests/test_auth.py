import time

import jwt
import pytest

from nas_tasker.auth import AccessVerifier, check_bearer
from nas_tasker.errors import ToolError

from .jwt_helpers import AUD, ISSUER, KEY, OTHER_KEY, make_jwt, resolver

ALLOWED = frozenset({"menkinds@gmail.com", "reservoir.crabs@gmail.com"})
BEARER = "t" * 40
GBRAIN_TOKEN = "g" * 40


@pytest.fixture
def verifier():
    return AccessVerifier(ISSUER, AUD, ALLOWED, key_resolver=resolver)


def code_of(fn):
    with pytest.raises(ToolError) as exc:
        fn()
    return exc.value.code


def test_valid_token_returns_lowercased_email(verifier):
    assert verifier.verify(make_jwt("Menkinds@Gmail.com")) == "menkinds@gmail.com"


@pytest.mark.parametrize(
    "token",
    [
        None,
        "",
        "not-a-jwt",
        make_jwt(key=OTHER_KEY),
        make_jwt(aud="other-aud"),
        make_jwt(iss="https://evil.cloudflareaccess.com"),
        make_jwt(exp_in=-10),
        make_jwt(nbf=int(time.time()) + 3600),
        jwt.encode({"iss": ISSUER, "aud": AUD, "email": "menkinds@gmail.com"}, KEY, algorithm="RS256"),
        jwt.encode({"iss": ISSUER, "aud": AUD, "email": "menkinds@gmail.com",
                    "exp": int(time.time()) + 60}, "h" * 40, algorithm="HS256"),
    ],
    ids=["none", "empty", "garbage", "bad-signature", "bad-aud", "bad-iss", "expired",
         "not-yet-valid", "no-exp", "hs256"],
)
def test_invalid_tokens_unauthenticated(verifier, token):
    assert code_of(lambda: verifier.verify(token)) == "UNAUTHENTICATED"


def test_email_outside_allowlist_forbidden(verifier):
    assert code_of(lambda: verifier.verify(make_jwt("stranger@gmail.com"))) == "FORBIDDEN"


def test_service_token_without_email_forbidden(verifier):
    token = make_jwt(None, common_name="abc.access")
    assert code_of(lambda: verifier.verify(token)) == "FORBIDDEN"


def test_key_lookup_failure_is_unauthenticated():
    def broken(token):
        raise OSError("jwks down")

    v = AccessVerifier(ISSUER, AUD, ALLOWED, key_resolver=broken)
    assert code_of(lambda: v.verify(make_jwt())) == "UNAUTHENTICATED"


def test_bearer_ok():
    check_bearer(f"Bearer {BEARER}", BEARER)
    check_bearer(f"bearer {BEARER}", BEARER)


@pytest.mark.parametrize(
    "header",
    [None, "", "Bearer", f"Basic {BEARER}", "Bearer wrong", f"Bearer {GBRAIN_TOKEN}", BEARER],
)
def test_bearer_rejected(header):
    assert code_of(lambda: check_bearer(header, BEARER)) == "UNAUTHENTICATED"
