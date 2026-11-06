import json

from django.test import AsyncClient

from comments import captcha, tokens
from comments.models import Author, Comment

CREATE = """
mutation Create($input: CommentInput!, $attachment: Upload) {
  createComment(input: $input, attachment: $attachment) {
    comment {
      id parentId rootId username email homePage text attachment attachmentType createdAt replyCount
    }
    token
    tokenExpiresIn
  }
}
"""


async def graphql(query, variables=None, headers=None):
    response = await AsyncClient().post(
        "/graphql/",
        data=json.dumps({"query": query, "variables": variables or {}}),
        content_type="application/json",
        headers=headers,
    )
    return json.loads(response.content)


def error_of(payload):
    return payload["errors"][0]["extensions"]


def solved_captcha():
    return {"captchaKey": captcha.store.add("AbC12"), "captchaValue": "AbC12"}


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
