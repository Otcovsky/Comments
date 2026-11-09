import itertools
import json
from contextlib import contextmanager
from unittest import mock

import redis
import redis.asyncio
from django.test import AsyncClient

from comments import captcha, live, shared, tokens
from comments.models import Author, Comment

CREATE = """
mutation Create($input: CommentInput!, $attachment: Upload) {
  createComment(input: $input, attachment: $attachment) {
    comment {
      id parentId rootId username email homePage text attachment attachmentName attachmentType createdAt replyCount
    }
    token
    tokenExpiresIn
  }
}
"""


_addresses = itertools.count(1)


def own_address():
    """A new client address, so that the tests stay under the limits per IP."""
    number = next(_addresses)
    return {"X-Real-IP": f"10.0.{number // 256}.{number % 256}"}


async def graphql(query, variables=None, headers=None):
    response = await AsyncClient().post(
        "/graphql/",
        data=json.dumps({"query": query, "variables": variables or {}}),
        content_type="application/json",
        headers={**own_address(), **(headers or {})},
    )
    # The events go out after the response; a test sees the state once they are sent.
    await live.finish_dispatching()
    return json.loads(response.content)


@contextmanager
def redis_down():
    """Every call to Redis fails at once, as with Redis stopped. The channel layer keeps its
    own connections and still works."""
    url = "redis://127.0.0.1:1/0"  # nothing listens there
    blocking = redis.Redis.from_url(url)
    with (
        mock.patch.object(shared, "client", lambda: blocking),
        mock.patch.object(shared, "aclient", lambda: redis.asyncio.Redis.from_url(url)),
    ):
        yield


def error_of(payload):
    return payload["errors"][0]["extensions"]


async def solved_captcha():
    return {"captchaKey": await captcha.store.add("AbC12"), "captchaValue": "AbC12"}


def form(username="bob", email="bob@example.com", home_page="", text="Hello", parent_id=None, **extra):
    return {
        "username": username,
        "email": email,
        "homePage": home_page,
        "text": text,
        "parentId": parent_id,
        "captchaKey": None,
        "captchaValue": None,
        **extra,
    }


def bearer(username="bob", email="bob@example.com"):
    return {"Authorization": f"Bearer {tokens.issue(username, email)}"}


async def post(username="bob", email="bob@example.com", text="Hello", parent_id=None):
    """Posts through the API with a token, the way a returning author does."""
    variables = {"input": form(username, email, text=text, parent_id=parent_id)}
    return (await graphql(CREATE, variables, bearer(username, email)))["data"]["createComment"]["comment"]


def make_author(username="bob", email="bob@example.com", home_page=""):
    return Author.objects.create(username=username, email=email, home_page=home_page)


def make_comment(author, parent=None, **fields):
    root = None
    if parent is not None:
        root = parent.root or parent
    return Comment.objects.create(author=author, parent=parent, root=root, text=fields.pop("text", "x"), **fields)
