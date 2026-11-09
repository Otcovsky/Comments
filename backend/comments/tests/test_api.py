import ipaddress
import json
from datetime import datetime, timedelta, timezone
from io import BytesIO
from unittest import mock

from asgiref.sync import sync_to_async
from botocore.exceptions import ClientError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import AsyncClient, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from PIL import Image

from comments import cache, captcha, live, queries, ratelimit, shared, tasks
from comments.models import Author, Comment

from .helpers import (
    CREATE,
    bearer,
    error_of,
    form,
    graphql,
    make_author,
    make_comment,
    own_address,
    post,
    redis_down,
    solved_captcha,
)

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
        payload = await self.create(text="<i>hi</i>", home_page="https://bob.io", **await solved_captcha())
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
        first = (await self.create(**await solved_captcha()))["data"]["createComment"]
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
        payload = await self.create(**{**await solved_captcha(), "captchaValue": "wrong"})
        self.assertIn("captchaValue", error_of(payload)["fields"])

    async def test_captcha_is_spent_by_a_failed_attempt(self):
        captcha = await solved_captcha()
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


class RateLimitTests(TransactionTestCase):
    async def test_comments_over_the_limit_are_refused_and_keep_the_captcha(self):
        address = {"X-Real-IP": "198.51.100.1"}
        with mock.patch.object(ratelimit.comments, "limit", 1):
            await graphql(CREATE, {"input": form(**await solved_captcha())}, address)
            second = await solved_captcha()
            payload = await graphql(CREATE, {"input": form(**second)}, address)
        self.assertEqual(error_of(payload)["code"], "RATE_LIMITED")
        self.assertIn("rateLimit", error_of(payload)["fields"])
        self.assertEqual(await Comment.objects.acount(), 1)
        self.assertTrue(await captcha.store.check(second["captchaKey"], second["captchaValue"]))

    async def test_captchas_over_the_limit_are_refused_and_aliases_count(self):
        query = "mutation { a: issueCaptcha { key } b: issueCaptcha { key } }"
        with mock.patch.object(ratelimit.captchas, "limit", 1):
            payload = await graphql(query, headers={"X-Real-IP": "198.51.100.2"})
        self.assertEqual(error_of(payload)["code"], "RATE_LIMITED")

    async def test_another_address_has_its_own_limit(self):
        query = "mutation { issueCaptcha { key } }"
        with mock.patch.object(ratelimit.captchas, "limit", 1):
            await graphql(query, headers={"X-Real-IP": "198.51.100.3"})
            payload = await graphql(query, headers={"X-Real-IP": "198.51.100.4"})
        self.assertNotIn("errors", payload)

    @override_settings(RATE_LIMIT_EXEMPT=ipaddress.ip_network("198.51.100.0/28"))
    async def test_load_machine_is_exempt(self):
        address = {"X-Real-IP": "198.51.100.5"}
        with mock.patch.object(ratelimit.comments, "limit", 1), mock.patch.object(ratelimit.captchas, "limit", 1):
            for _ in range(2):
                self.assertNotIn("errors", await graphql("mutation { issueCaptcha { key } }", headers=address))
                self.assertNotIn("errors", await graphql(CREATE, {"input": form(**await solved_captcha())}, address))


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
            headers={**own_address(), **(bearer() if headers is None else headers)},
        )
        await live.finish_dispatching()
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

    async def test_file_of_a_comment_not_saved_is_deleted(self):
        failure = RuntimeError("database down")
        with self.bucket() as client, mock.patch.object(queries, "save_comment", side_effect=failure):
            with self.assertLogs("strawberry.execution", "ERROR"):
                payload = await self.send("a.txt", b"hello")
        self.assertIsNone(payload["data"])
        key = client.return_value.put_object.call_args.kwargs["Key"]
        client.return_value.delete_object.assert_called_once_with(Bucket=mock.ANY, Key=key)

    def test_delete_of_an_orphan_is_retried(self):
        failure = ClientError({"Error": {"Code": "500", "Message": "x"}}, "DeleteObject")
        with self.bucket(**{"delete_object.side_effect": [failure, None]}) as client:
            tasks.delete_attachment.delay("attachments/2026/11/x.txt")
        self.assertEqual(client.return_value.delete_object.call_count, 2)

    async def test_file_is_not_touched_when_the_form_is_wrong(self):
        with self.bucket() as client:
            payload = await self.send("a.txt", b"hello", {})
        self.assertIn("captchaValue", error_of(payload)["fields"])
        client.return_value.put_object.assert_not_called()


class RedisDownTests(TransactionTestCase):
    """Without Redis the comments are read and written; only captchas wait for it."""

    async def test_list_counts_in_the_database(self):
        author = await sync_to_async(make_author)()
        for _ in range(3):
            await sync_to_async(make_comment)(author)
        with redis_down():
            data = (await graphql(COMMENTS, list_variables(page_size=2)))["data"]["comments"]
        self.assertEqual(data["count"], 3)
        self.assertEqual(len(data["results"]), 2)

    async def test_author_with_a_token_posts_once(self):
        # Every limit is off, the count is not forgotten, and the events cannot be sent: none
        # of that may turn a saved comment into an error the author would retry.
        shared._warned.clear()
        with redis_down(), self.assertLogs("comments", "WARNING"):
            payload = await graphql(CREATE, {"input": form()}, bearer())
        self.assertNotIn("errors", payload)
        self.assertEqual(await Comment.objects.acount(), 1)

    async def test_captcha_cannot_be_checked(self):
        solved = await solved_captcha()
        with redis_down():
            payload = await graphql(CREATE, {"input": form(**solved)})
        self.assertEqual(error_of(payload)["code"], "VALIDATION")
        self.assertIn("Обновите картинку", error_of(payload)["fields"]["captchaValue"])
        self.assertEqual(await Comment.objects.acount(), 0)

    async def test_no_captcha_is_issued(self):
        # A server failure for the client: it shows a general error.
        with redis_down(), self.assertLogs("strawberry.execution", "ERROR"):
            payload = await graphql("mutation { issueCaptcha { key } }")
        self.assertIsNone(payload["data"])
        self.assertNotIn("redis", json.dumps(payload).lower())


class TopLevelCountTests(TransactionTestCase):
    def test_counted_once_until_a_top_level_comment_is_saved(self):
        author = make_author()
        root = make_comment(author)
        with self.assertNumQueries(1):
            self.assertEqual(queries.top_level_count.get(), 1)
            self.assertEqual(queries.top_level_count.get(), 1)
        make_comment(author, parent=root)
        with self.assertNumQueries(0):
            self.assertEqual(queries.top_level_count.get(), 1)
        make_comment(author)
        with self.assertNumQueries(0):
            self.assertEqual(queries.top_level_count.get(), 2)
        root.delete()
        self.assertEqual(queries.top_level_count.get(), 1)

    def test_a_new_comment_carries_the_count_without_extending_its_life(self):
        author = make_author()
        make_comment(author)
        queries.top_level_count.get()
        client = shared.client()
        old = int(client.get(queries.TopLevelCount.GENERATION_KEY) or 0)
        client.pexpire(f"top_level_count:{old}", 5000)
        make_comment(author)
        new = int(client.get(queries.TopLevelCount.GENERATION_KEY))
        self.assertEqual(new, old + 1)
        self.assertEqual(client.get(f"top_level_count:{new}"), "2")
        self.assertLessEqual(client.pttl(f"top_level_count:{new}"), 5000)

    def test_a_new_comment_without_a_count_leaves_it_to_be_counted(self):
        author = make_author()
        make_comment(author)
        make_comment(author)
        with self.assertNumQueries(1):
            self.assertEqual(queries.top_level_count.get(), 2)

    def test_without_redis_it_is_counted_every_time_and_a_forget_does_not_fail(self):
        make_comment(make_author())
        with redis_down():
            queries.top_level_count.forget()
            with self.assertNumQueries(2):
                self.assertEqual(queries.top_level_count.get(), 1)
                self.assertEqual(queries.top_level_count.get(), 1)

    def test_a_missed_forget_lives_a_minute_at_most(self):
        make_comment(make_author())
        queries.top_level_count.get()
        generation = shared.client().get(queries.TopLevelCount.GENERATION_KEY) or 0
        self.assertLessEqual(shared.client().ttl(f"top_level_count:{generation}"), 60)

    def test_a_count_older_than_a_new_comment_is_not_kept(self):
        author = make_author()
        make_comment(author)
        queries.top_level_count.forget()
        real_count = queries._top_level

        def count_while_another_comment_is_saved():
            counted = real_count()
            make_comment(author)
            return mock.Mock(count=lambda: counted.count() - 1)

        with mock.patch.object(queries, "_top_level", count_while_another_comment_is_saved):
            self.assertEqual(queries.top_level_count.get(), 1)
        self.assertEqual(queries.top_level_count.get(), 2)


class CacheTests(TransactionTestCase):
    """The cache answers what the database would, and a write is seen by the next read."""

    def setUp(self):
        self.ann = make_author("ann", "ann@example.com")

    def page(self, page=1, page_size=25, ordering="CREATED_AT_DESC"):
        return queries.comment_page(page, page_size, ordering)

    def test_a_page_read_again_needs_no_database(self):
        first = make_comment(self.ann, attachment="attachments/2026/11/a.jpg", attachment_type="image")
        make_comment(self.ann, parent=first)
        cold = self.page()
        with self.assertNumQueries(0):
            warm = self.page()
        self.assertEqual(warm, cold)
        self.assertEqual(warm[1][0]["reply_count"], 1)
        self.assertEqual(warm[1][0]["created_at"], first.created_at)
        cold = queries.replies(first.pk)
        with self.assertNumQueries(0):
            self.assertEqual(queries.replies(first.pk), cold)

    def test_cached_rows_are_the_rows_of_the_database(self):
        root = make_comment(make_author("bob", "bob@example.com", "https://bob.io"), text="<i>x</i>")
        make_comment(self.ann, parent=root)
        self.page()
        self.assertEqual(self.page(), queries._page_from_database(1, 25, "CREATED_AT_DESC"))
        queries.replies(root.pk)
        self.assertEqual(queries.replies(root.pk), queries._replies_from_database(root.pk))

    def test_a_new_top_level_comment_is_on_the_next_read(self):
        make_comment(self.ann)
        self.page()
        new = make_comment(self.ann)
        count, rows = self.page()
        self.assertEqual((count, rows[0]["id"]), (2, new.pk))

    def test_a_new_reply_is_seen_and_other_branches_stay_cached(self):
        first, second = make_comment(self.ann), make_comment(self.ann)
        self.page()
        queries.replies(first.pk)
        queries.replies(second.pk)
        reply = make_comment(self.ann, parent=first)
        by_id = {row["id"]: row for row in self.page()[1]}
        self.assertEqual((by_id[first.pk]["reply_count"], by_id[second.pk]["reply_count"]), (1, 0))
        self.assertEqual([row["id"] for row in queries.replies(first.pk)], [reply.pk])
        with self.assertNumQueries(0):
            queries.replies(second.pk)

    def test_a_reply_saved_while_its_branch_is_read_is_not_lost(self):
        root = make_comment(self.ann)
        real = queries._rows_from_database

        def read_then_reply(ids):
            rows = real(ids)  # the count is read before the reply is saved
            make_comment(self.ann, parent=root)
            return rows

        with mock.patch.object(queries, "_rows_from_database", read_then_reply):
            self.assertEqual(self.page()[1][0]["reply_count"], 0)
        self.assertEqual(self.page()[1][0]["reply_count"], 1)

    def test_a_comment_saved_while_a_page_is_read_is_not_lost(self):
        make_comment(self.ann)
        real = queries._page_ids

        def list_then_post(*args):
            ids = real(*args)
            make_comment(self.ann)
            return ids

        with mock.patch.object(queries, "_page_ids", list_then_post):
            self.assertEqual(len(self.page()[1]), 1)
        self.assertEqual(len(self.page()[1]), 2)

    def test_deleted_comments_are_gone(self):
        root = make_comment(self.ann)
        reply = make_comment(self.ann, parent=root)
        self.page()
        queries.replies(root.pk)
        reply.delete()
        self.assertEqual(queries.replies(root.pk), [])
        self.assertEqual(self.page()[1][0]["reply_count"], 0)
        root.delete()
        self.assertEqual(self.page(), (0, []))

    def test_a_flushed_database_meets_no_old_rows(self):
        make_comment(self.ann, text="old")
        self.page()
        Comment.objects.update(text="new")  # as rows of a new database with the same ids
        cache.forget_all()
        self.assertEqual(self.page()[1][0]["text"], "new")

    def test_without_redis_replies_come_from_the_database(self):
        root = make_comment(self.ann)
        make_comment(self.ann, parent=root)
        with redis_down():
            self.assertEqual(len(queries.replies(root.pk)), 1)
            self.assertEqual(self.page()[1][0]["reply_count"], 1)


class PositionTests(TransactionTestCase):
    """The count behind the page of a live event must agree with the real order of the list."""

    def test_comment_keeps_its_authors_pair(self):
        comment = make_comment(make_author("ann", "ann@example.com"))
        comment.refresh_from_db()
        self.assertEqual((comment.username, comment.email), ("ann", "ann@example.com"))

    def test_sorting_and_counting_by_name_need_no_author_table(self):
        comment = make_comment(make_author())
        for ordering in ("USERNAME_ASC", "USERNAME_DESC", "EMAIL_ASC", "EMAIL_DESC"):
            with self.subTest(ordering=ordering), CaptureQueriesContext(connection) as captured:
                queries.positions(comment.pk, {ordering: 100})
                queries.comment_page(1, 25, ordering)
            # The positions and the ids of the page; only the page's own rows join authors.
            sorting = [q["sql"] for q in captured.captured_queries if "home_page" not in q["sql"]]
            self.assertTrue(sorting)
            for sql in sorting:
                self.assertNotIn("comments_author", sql)

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
                    found = queries.positions(comment.pk, dict.fromkeys(orderings, 100))
                    self.assertEqual(found[ordering], order.index(comment.pk))

    def test_position_is_counted_up_to_the_limit(self):
        author = make_author()
        comments = [make_comment(author) for _ in range(6)]
        oldest, newest = comments[0].pk, comments[-1].pk
        # By date, newest first: the oldest comment has five before it, and ties count too.
        Comment.objects.update(created_at=datetime(2020, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(queries.positions(oldest, {"CREATED_AT_DESC": 100}), {"CREATED_AT_DESC": 5})
        self.assertEqual(queries.positions(oldest, {"CREATED_AT_DESC": 3}), {"CREATED_AT_DESC": 3})
        self.assertEqual(queries.positions(newest, {"CREATED_AT_DESC": 3}), {"CREATED_AT_DESC": 0})
