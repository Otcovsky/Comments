import json
from datetime import datetime, timedelta, timezone
from io import BytesIO
from unittest import mock

from asgiref.sync import sync_to_async
from botocore.exceptions import ClientError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import AsyncClient, TransactionTestCase
from PIL import Image

from comments import queries
from comments.models import Author, Comment

from .helpers import CREATE, bearer, error_of, form, graphql, make_author, make_comment, post, solved_captcha

COMMENTS = """
query Comments($page: Int!, $pageSize: Int!, $ordering: CommentOrdering!) {
  comments(page: $page, pageSize: $pageSize, ordering: $ordering) {
    count
    results { id username attachment attachmentType createdAt replyCount parentId rootId homePage }
  }
}
"""
REPLIES = "query R($parentId: ID!) { replies(parentId: $parentId) { id parentId rootId replyCount } }"


def list_variables(page=1, page_size=25, ordering="CREATED_AT_DESC"):
    return {"page": page, "pageSize": page_size, "ordering": ordering}


class ListTests(TransactionTestCase):
    def setUp(self):
        self.ann = make_author("ann", "z@example.com", "https://ann.io")
        self.bob = make_author("bob", "a@example.com")

    async def results(self, **options):
        payload = await graphql(COMMENTS, list_variables(**options))
        return payload["data"]["comments"]

    async def ids(self, **options):
        return [row["id"] for row in (await self.results(**options))["results"]]

    async def test_empty_first_page_is_not_an_error(self):
        await sync_to_async(Comment.objects.all().delete)()
        self.assertEqual(await self.results(), {"count": 0, "results": []})

    async def test_only_top_level_with_direct_reply_count(self):
        first = await sync_to_async(make_comment)(self.ann)
        reply = await sync_to_async(make_comment)(self.bob, first)
        await sync_to_async(make_comment)(self.bob, reply)
        data = await self.results()
        self.assertEqual(data["count"], 1)
        row = data["results"][0]
        self.assertEqual(row["id"], str(first.id))
        self.assertEqual(row["replyCount"], 1)
        self.assertIsNone(row["parentId"])
        self.assertIsNone(row["rootId"])
        self.assertEqual(row["homePage"], "https://ann.io")
        self.assertIsNotNone(datetime.fromisoformat(row["createdAt"]).tzinfo)

    async def test_orderings(self):
        a = await sync_to_async(make_comment)(self.ann)
        b = await sync_to_async(make_comment)(self.bob)
        self.assertEqual(await self.ids(ordering="CREATED_AT_DESC"), [str(b.id), str(a.id)])
        self.assertEqual(await self.ids(ordering="CREATED_AT_ASC"), [str(a.id), str(b.id)])
        self.assertEqual(await self.ids(ordering="USERNAME_ASC"), [str(a.id), str(b.id)])
        self.assertEqual(await self.ids(ordering="USERNAME_DESC"), [str(b.id), str(a.id)])
        self.assertEqual(await self.ids(ordering="EMAIL_ASC"), [str(b.id), str(a.id)])
        self.assertEqual(await self.ids(ordering="EMAIL_DESC"), [str(a.id), str(b.id)])

    async def test_ties_go_to_the_newer_id_in_any_direction(self):
        a = await sync_to_async(make_comment)(self.ann)
        b = await sync_to_async(make_comment)(self.ann)
        self.assertEqual(await self.ids(ordering="USERNAME_ASC"), [str(b.id), str(a.id)])
        self.assertEqual(await self.ids(ordering="USERNAME_DESC"), [str(b.id), str(a.id)])

    async def test_pages(self):
        comments = [await sync_to_async(make_comment)(self.ann) for _ in range(5)]
        self.assertEqual(
            await self.ids(page=2, page_size=2), [str(comments[2].id), str(comments[1].id)]
        )
        self.assertEqual(await self.ids(page=3, page_size=2), [str(comments[0].id)])

    async def test_page_past_the_end(self):
        await sync_to_async(make_comment)(self.ann)
        payload = await graphql(COMMENTS, list_variables(page=2))
        self.assertIsNone(payload["data"])
        self.assertEqual(error_of(payload)["code"], "PAGE_NOT_FOUND")
        self.assertEqual(error_of(await graphql(COMMENTS, list_variables(page=0)))["code"], "PAGE_NOT_FOUND")

    async def test_page_size_limits(self):
        for size in (0, 101):
            self.assertEqual(
                error_of(await graphql(COMMENTS, list_variables(page_size=size)))["code"], "INVALID_PAGE_SIZE"
            )
        self.assertEqual((await self.results(page_size=100))["count"], 0)

    async def test_attachment_link(self):
        await sync_to_async(make_comment)(
            self.ann, attachment="attachments/2026/11/a.jpg", attachment_type="image"
        )
        await sync_to_async(make_comment)(self.bob)
        rows = (await self.results(ordering="CREATED_AT_ASC"))["results"]
        self.assertEqual(rows[0]["attachment"], "/media/attachments/2026/11/a.jpg")
        self.assertEqual(rows[0]["attachmentType"], "IMAGE")
        self.assertIsNone(rows[1]["attachment"])
        self.assertIsNone(rows[1]["attachmentType"])

    async def test_replies_are_direct_and_newest_first(self):
        root = await sync_to_async(make_comment)(self.ann)
        first = await sync_to_async(make_comment)(self.bob, root)
        second = await sync_to_async(make_comment)(self.bob, root)
        deep = await sync_to_async(make_comment)(self.bob, first)
        rows = (await graphql(REPLIES, {"parentId": str(root.id)}))["data"]["replies"]
        self.assertEqual([row["id"] for row in rows], [str(second.id), str(first.id)])
        self.assertEqual(rows[1]["replyCount"], 1)
        self.assertEqual(rows[1]["rootId"], str(root.id))
        rows = (await graphql(REPLIES, {"parentId": str(first.id)}))["data"]["replies"]
        self.assertEqual(rows[0]["id"], str(deep.id))
        self.assertEqual(rows[0]["rootId"], str(root.id))

    async def test_replies_of_nothing(self):
        for parent_id in ("999999", "abc"):
            self.assertEqual((await graphql(REPLIES, {"parentId": parent_id}))["data"]["replies"], [])


class PreviewTests(TransactionTestCase):
    async def test_returns_sanitized_text_and_saves_nothing(self):
        payload = await graphql(
            "query P($text: String!) { previewText(text: $text) }", {"text": "<i>a</i> < b"}
        )
        self.assertEqual(payload["data"]["previewText"], "<i>a</i> &lt; b")
        self.assertEqual(await Comment.objects.acount(), 0)

    async def test_error(self):
        payload = await graphql("query P($text: String!) { previewText(text: $text) }", {"text": "<b>a</b>"})
        self.assertEqual(error_of(payload)["code"], "VALIDATION")
        self.assertIn("text", error_of(payload)["fields"])


class CaptchaTests(TransactionTestCase):
    async def test_issue(self):
        data = (await graphql("mutation { issueCaptcha { key image } }"))["data"]["issueCaptcha"]
        self.assertEqual(len(data["key"]), 32)
        self.assertTrue(data["image"].startswith("data:image/png;base64,"))


class CreateTests(TransactionTestCase):
    async def create(self, headers=None, **fields):
        return await graphql(CREATE, {"input": form(**fields)}, headers)

    async def test_with_captcha(self):
        payload = await self.create(text="<i>hi</i>", home_page="https://bob.io", **solved_captcha())
        result = payload["data"]["createComment"]
        comment = result["comment"]
        self.assertEqual(comment["username"], "bob")
        self.assertEqual(comment["homePage"], "https://bob.io")
        self.assertEqual(comment["text"], "<i>hi</i>")
        self.assertEqual(comment["replyCount"], 0)
        self.assertIsNone(comment["parentId"])
        self.assertIsNone(comment["attachment"])
        self.assertTrue(result["token"])
        self.assertEqual(result["tokenExpiresIn"], 3600)

    async def test_token_replaces_the_captcha(self):
        first = (await self.create(**solved_captcha()))["data"]["createComment"]
        headers = {"Authorization": f"Bearer {first['token']}"}
        second = (await self.create(headers))["data"]["createComment"]
        self.assertTrue(second["comment"]["id"])
        self.assertIsNone(second["token"])
        self.assertIsNone(second["tokenExpiresIn"])

    async def test_without_captcha_or_token(self):
        payload = await self.create()
        self.assertEqual(error_of(payload)["code"], "VALIDATION")
        self.assertIn("captchaValue", error_of(payload)["fields"])

    async def test_token_of_another_author(self):
        payload = await self.create(bearer("ann", "ann@example.com"))
        self.assertIn("captchaValue", error_of(payload)["fields"])

    async def test_wrong_captcha(self):
        payload = await self.create(**{**solved_captcha(), "captchaValue": "wrong"})
        self.assertIn("captchaValue", error_of(payload)["fields"])

    async def test_captcha_is_spent_by_a_failed_attempt(self):
        captcha = solved_captcha()
        payload = await self.create(username="не латиница", **captcha)
        self.assertEqual(list(error_of(payload)["fields"]), ["username"])
        self.assertIn("captchaValue", error_of(await self.create(**captcha))["fields"])

    async def test_all_field_errors_come_together(self):
        payload = await self.create(username="", email="bad", home_page="ftp://x.io", text="<b>x</b>")
        self.assertEqual(
            set(error_of(payload)["fields"]), {"username", "email", "homePage", "text", "captchaValue"}
        )
        self.assertIsNone(payload["data"])

    async def test_reply_gets_parent_and_root(self):
        root = await post()
        reply = await post(parent_id=root["id"])
        deep = await post(parent_id=reply["id"])
        self.assertEqual((reply["parentId"], reply["rootId"]), (root["id"], root["id"]))
        self.assertEqual((deep["parentId"], deep["rootId"]), (reply["id"], root["id"]))

    async def test_unknown_parent(self):
        for parent_id in ("999999", "abc"):
            payload = await self.create(bearer(), parent_id=parent_id)
            self.assertIn("parentId", error_of(payload)["fields"])
        self.assertEqual(await Comment.objects.acount(), 0)

    async def test_author_is_created_once_and_keeps_the_first_home_page(self):
        await self.create(bearer(), home_page="https://first.io")
        payload = await self.create(bearer(), home_page="https://second.io")
        self.assertEqual(payload["data"]["createComment"]["comment"]["homePage"], "https://first.io")
        self.assertEqual(await Author.objects.acount(), 1)

    async def test_stores_the_client(self):
        await self.create({**bearer(), "X-Real-IP": "203.0.113.7", "User-Agent": "a" * 300})
        comment = await Comment.objects.afirst()
        self.assertEqual(comment.ip_address, "203.0.113.7")
        self.assertEqual(len(comment.user_agent), 255)

    async def test_too_many_aliases(self):
        query = "mutation { " + " ".join(f"c{i}: issueCaptcha {{ key }}" for i in range(6)) + " }"
        self.assertIn("errors", await graphql(query))

    async def test_failure_details_stay_on_the_server(self):
        with mock.patch.object(queries, "comment_page", side_effect=RuntimeError("secret")), self.assertLogs(
            "strawberry.execution", "ERROR"
        ):
            payload = await graphql(COMMENTS, list_variables())
        self.assertNotIn("secret", json.dumps(payload))
        self.assertIsNone(payload["data"])


def png():
    buffer = BytesIO()
    Image.new("RGB", (800, 600), "red").save(buffer, format="PNG")
    return buffer.getvalue()


class UploadTests(TransactionTestCase):
    async def send(self, name, content, headers=None):
        """The GraphQL multipart request, as in frontend/src/api.js."""
        operations = {"query": CREATE, "variables": {"input": form(), "attachment": None}}
        response = await AsyncClient().post(
            "/graphql/",
            data={
                "operations": json.dumps(operations),
                "map": json.dumps({"0": ["variables.attachment"]}),
                "0": SimpleUploadedFile(name, content),
            },
            headers=bearer() if headers is None else headers,
        )
        return json.loads(response.content)

    def bucket(self, **options):
        return mock.patch("comments.storage._client", return_value=mock.Mock(**options))

    async def test_image(self):
        with self.bucket() as client:
            payload = await self.send("a.png", png())
        comment = payload["data"]["createComment"]["comment"]
        self.assertEqual(comment["attachmentType"], "IMAGE")
        self.assertRegex(comment["attachment"], r"^/media/attachments/\d{4}/\d{2}/[0-9a-f]{32}\.png$")
        stored = client.return_value.put_object.call_args.kwargs
        self.assertEqual(f"/media/{stored['Key']}", comment["attachment"])
        self.assertEqual(stored["ContentType"], "image/png")
        self.assertEqual(Image.open(BytesIO(stored["Body"])).size, (320, 240))

    async def test_text(self):
        with self.bucket() as client:
            payload = await self.send("a.txt", b"hello")
        self.assertEqual(payload["data"]["createComment"]["comment"]["attachmentType"], "TEXT")
        self.assertEqual(client.return_value.put_object.call_args.kwargs["Body"], b"hello")

    async def test_bad_file_means_no_comment(self):
        with self.bucket() as client:
            payload = await self.send("a.png", b"not an image")
        self.assertIn("attachment", error_of(payload)["fields"])
        client.return_value.put_object.assert_not_called()
        self.assertEqual(await Comment.objects.acount(), 0)

    async def test_bucket_failure_means_no_comment(self):
        failure = ClientError({"Error": {"Code": "500", "Message": "x"}}, "PutObject")
        client = mock.Mock(**{"put_object.side_effect": failure})
        with mock.patch("comments.storage._client", return_value=client), self.assertLogs("comments.schema", "ERROR"):
            payload = await self.send("a.txt", b"hello")
        self.assertIn("attachment", error_of(payload)["fields"])
        self.assertEqual(await Comment.objects.acount(), 0)

    async def test_file_is_not_touched_when_the_form_is_wrong(self):
        with self.bucket() as client:
            payload = await self.send("a.txt", b"hello", {})
        self.assertIn("captchaValue", error_of(payload)["fields"])
        client.return_value.put_object.assert_not_called()


class PositionTests(TransactionTestCase):
    """The count behind the page of a live event must agree with the real order of the list."""

    def test_position_matches_the_page_query(self):
        authors = [make_author(name, f"{name}@example.com") for name in ("ann", "Bob", "cid")]
        comments = [make_comment(authors[index % 3]) for index in range(12)]
        old = datetime(2020, 1, 1, tzinfo=timezone.utc)
        Comment.objects.filter(pk=comments[3].pk).update(created_at=old)
        Comment.objects.filter(pk=comments[4].pk).update(created_at=old + timedelta(days=1))
        orderings = list(queries.ORDERINGS)
        for ordering in orderings:
            _, rows = queries.comment_page(1, 100, ordering)
            order = [row["id"] for row in rows]
            for comment in comments:
                with self.subTest(ordering=ordering, comment=comment.pk):
                    found = queries.positions(comment.pk, orderings)
                    self.assertEqual(found[ordering], order.index(comment.pk))
