import time

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

ISSUER = "https://myteam.cloudflareaccess.com"
AUD = "aud-123"

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def resolver(token):
    return KEY.public_key()


def make_jwt(email="menkinds@gmail.com", *, key=KEY, iss=ISSUER, aud=AUD, exp_in=300, **extra):
    now = int(time.time())
    claims = {"iss": iss, "aud": [aud], "iat": now, "nbf": now, "exp": now + exp_in, **extra}
    if email is not None:
        claims["email"] = email
    return jwt.encode(claims, key, algorithm="RS256")
