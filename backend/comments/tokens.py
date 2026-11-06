"""A signed token for a solved captcha: the same author skips the next ones until it expires.

There are no accounts, and the token does not prove who the author is. It is not renewed by
using it, so a new captcha is due at least once per lifetime.
"""
from django.conf import settings
from django.core import signing

SALT = "comments.author-token"


def issue(username, email):
    return signing.dumps([username, email], salt=SALT)


def allows(header, username, email):
    """Whether the Authorization header carries a live token of this author. A bad token is
    not an error of its own: the author just gets a captcha, as without a token."""
    scheme, _, token = (header or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        return False
    try:
        return signing.loads(token.strip(), salt=SALT, max_age=settings.AUTHOR_TOKEN_TTL) == [username, email]
    except signing.BadSignature:
        return False
