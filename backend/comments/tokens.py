"""A JWT for a solved captcha: the same author skips the next ones until it expires.

There are no accounts, and the token does not prove who the author is. It is not renewed by
using it, so a new captcha is due at least once per lifetime.
"""
from datetime import datetime, timedelta, timezone

import jwt
from django.conf import settings
from django.utils.crypto import salted_hmac

SALT = "comments.author-token"
AUDIENCE = "comments.author"
ALGORITHM = "HS256"


def _key():
    # Derived from SECRET_KEY, so a leaked token says nothing about it or about other
    # signatures made with it, and the key is always as long as the hash.
    return salted_hmac(SALT, "", algorithm="sha256").digest()


def issue(username, email):
    now = datetime.now(timezone.utc)
    claims = {
        "sub": username,
        "email": email,
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + timedelta(seconds=settings.AUTHOR_TOKEN_TTL),
    }
    return jwt.encode(claims, _key(), algorithm=ALGORITHM)


def allows(header, username, email):
    """Whether the Authorization header carries a live token of this author. A bad token is
    not an error of its own: the author just gets a captcha, as without a token."""
    scheme, _, token = (header or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        return False
    try:
        claims = jwt.decode(
            token.strip(),
            _key(),
            algorithms=[ALGORITHM],
            audience=AUDIENCE,
            options={"require": ["sub", "email", "iat", "exp"]},
        )
    except jwt.InvalidTokenError:
        return False
    return claims["sub"] == username and claims["email"] == email
