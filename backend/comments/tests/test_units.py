import ipaddress
import logging
import time
from unittest import mock

import jwt
import redis

from django.test import SimpleTestCase, override_settings

from comments import captcha, pools, ratelimit, shared, tokens, validation
from comments.errors import ApiError
from comments.live import Viewers, parse_viewport
from comments.sanitizer import MessageSanitizer, SanitizeError

from .helpers import redis_down


class SanitizerTests(SimpleTestCase):
    def test_keeps_allowed_markup(self):
        text = '<strong>a</strong> <i>b</i> <code>c</code> <a href="https://x.io/?a=1" title="t">d</a>'
        self.assertEqual(MessageSanitizer.clean(text), text)

    def test_escapes_text(self):
        self.assertEqual(MessageSanitizer.clean("1 < 2 & 3"), "1 &lt; 2 &amp; 3")

    def test_escapes_attribute_values(self):
        cleaned = MessageSanitizer.clean("""<a href="https://x.io" title='"><script>'>d</a>""")
        self.assertEqual(cleaned, '<a href="https://x.io" title="&quot;&gt;&lt;script&gt;">d</a>')

    def test_rejects(self):
        for text in (
            "<b>x</b>",
            "<script>alert(1)</script>",
            "<i>not closed",
            "</i>",
            "<i><strong>x</i></strong>",
            "<i/>",
            "<!-- x -->",
            "<!DOCTYPE html>",
            "<?php ?>",
            '<a href="javascript:alert(1)">x</a>',
            '<a href="&#106;avascript:alert(1)">x</a>',
            '<a href="https://a.io" onclick="x">x</a>',
            '<a href="https://a.io" href="https://b.io">x</a>',
            '<a href="https://a.io"><a href="https://b.io">x</a></a>',
            "<a>x</a>",
            '<i class="x">x</i>',
            "hello <world",
            "<i>x</i> <str",
            '<a href="https://a.io',
            "x </",
            "x <!-- y",
            "<![foo]>",
        ):
            with self.subTest(text), self.assertRaises(SanitizeError):
                MessageSanitizer.clean(text)


    def test_a_lone_angle_bracket_at_the_end_is_text(self):
        self.assertEqual(MessageSanitizer.clean("2 <"), "2 &lt;")
        self.assertEqual(MessageSanitizer.clean("a <1"), "a &lt;1")


class ValidationTests(SimpleTestCase):
    def test_username(self):
        self.assertIsNone(validation.check_username("Bob7"))
        self.assertIsNotNone(validation.check_username(""))
        self.assertIsNotNone(validation.check_username("Боб"))
        self.assertIsNotNone(validation.check_username("bob smith"))
        self.assertIsNotNone(validation.check_username("bob\n"))
        self.assertIsNotNone(validation.check_username("a" * 65))

    def test_email(self):
        self.assertIsNone(validation.check_email("a@b.io"))
        self.assertIsNotNone(validation.check_email("a@b"))
        self.assertIsNotNone(validation.check_email(""))

    def test_home_page(self):
        self.assertIsNone(validation.check_home_page(""))
        self.assertIsNone(validation.check_home_page("https://x.io/a"))
        self.assertIsNotNone(validation.check_home_page("ftp://x.io"))
        self.assertIsNotNone(validation.check_home_page("javascript:alert(1)"))
        self.assertIsNotNone(validation.check_home_page("https://x.io/" + "a" * 200))

    def test_text(self):
        with self.assertRaises(ValueError):
            validation.clean_text("   ")
        with self.assertRaises(ValueError):
            validation.clean_text("a" * 5001)
        self.assertEqual(validation.clean_text("a" * 5000), "a" * 5000)

    def test_preview_reports_the_text_field(self):
        with self.assertRaises(ApiError) as caught:
            validation.preview_text("<b>x</b>")
        self.assertEqual(caught.exception.extensions["code"], "VALIDATION")
        self.assertIn("text", caught.exception.extensions["fields"])

    def test_parse_id(self):
        self.assertEqual(validation.parse_id("123"), 123)
        for value in ("", "abc", "-1", " 1", "١٢", "1" * 20, 5, None):
            self.assertIsNone(validation.parse_id(value))


class RateLimiterTests(SimpleTestCase):
    def setUp(self):
        shared.client().flushdb()

    async def test_allows_up_to_the_limit(self):
        limiter = ratelimit.RateLimiter("test", 2)
        self.assertEqual([await limiter.allow("a") for _ in range(3)], [True, True, False])
        self.assertTrue(await limiter.allow("b"))

    async def test_the_window_expires(self):
        limiter = ratelimit.RateLimiter("test", 1)
        await limiter.allow("a")
        self.assertFalse(await limiter.allow("a"))
        ttl = await shared.aclient().ttl("rate:test:a")
        self.assertTrue(0 < ttl <= ratelimit.WINDOW_SECONDS, ttl)
        await shared.aclient().delete("rate:test:a")  # as Redis does when the window ends
        self.assertTrue(await limiter.allow("a"))

    async def test_limiters_count_apart(self):
        await ratelimit.RateLimiter("one", 1).allow("a")
        self.assertTrue(await ratelimit.RateLimiter("two", 1).allow("a"))

    def test_exempt_network(self):
        with override_settings(RATE_LIMIT_EXEMPT=ipaddress.ip_network("10.0.0.0/24")):
            self.assertTrue(ratelimit.is_exempt("10.0.0.7"))
            self.assertFalse(ratelimit.is_exempt("10.0.1.7"))
            self.assertFalse(ratelimit.is_exempt("not an address"))
            self.assertFalse(ratelimit.is_exempt(None))
        self.assertFalse(ratelimit.is_exempt("10.0.0.7"))


@override_settings(DB_THREADS=8)
class PoolStatsTests(SimpleTestCase):
    def test_reports_waits_and_runs_by_function_once(self):
        stats = pools.PoolStats()
        for _ in range(3):
            stats.submitted()
        for wait, run in ((0.001, 0.010), (0.003, 0.020)):
            stats.started()
            stats.finished("comment_page", wait, run)
        stats.started()
        stats.finished("positions", 0.050, 0.070)
        line = stats.report()
        self.assertTrue(line.startswith("db pool: busy max 1/8, queued max 3; positions n=1 "), line)
        self.assertIn("comment_page n=2 wait p50/p95/max 3.0/3.0/3.0 ms run p50/p95 20.0/20.0 ms", line)
        self.assertIsNone(stats.report())


class CaptchaStoreTests(SimpleTestCase):
    async def test_answer_ignores_case(self):
        store = captcha.CaptchaStore()
        self.assertTrue(await store.check(await store.add("AbC12"), "aBc12"))

    async def test_is_single_use_even_after_a_wrong_answer(self):
        store = captcha.CaptchaStore()
        key = await store.add("AbC12")
        self.assertFalse(await store.check(key, "wrong"))
        self.assertFalse(await store.check(key, "AbC12"))

    async def test_non_ascii_answer_is_just_wrong(self):
        store = captcha.CaptchaStore()
        self.assertFalse(await store.check(await store.add("AbC12"), "код"))

    async def test_expires(self):
        store = captcha.CaptchaStore()
        key = await store.add("AbC12")
        ttl = await shared.aclient().ttl(f"captcha:{key}")
        self.assertTrue(0 < ttl <= captcha.TTL_SECONDS, ttl)

    async def test_unknown_or_foreign_key(self):
        store = captcha.CaptchaStore()
        self.assertFalse(await store.check("0" * captcha.KEY_LENGTH, "AbC12"))
        self.assertFalse(await store.check("x" * 10_000, "AbC12"))

    def test_image_is_a_png_data_url(self):
        self.assertTrue(captcha.render("AbC12").startswith("data:image/png;base64,"))

    @override_settings(CAPTCHA_TEST_ANSWER="PERF1")
    async def test_answer_known_in_a_load_test_and_still_single_use(self):
        key, image = await captcha.issue()
        self.assertTrue(image.startswith("data:image/png;base64,"))
        self.assertTrue(await captcha.store.check(key, "PERF1"))
        self.assertFalse(await captcha.store.check(key, "PERF1"))


@override_settings(AUTHOR_TOKEN_TTL=3600)
class TokenTests(SimpleTestCase):
    def test_is_tied_to_the_author(self):
        header = f"Bearer {tokens.issue('bob', 'a@b.io')}"
        self.assertTrue(tokens.allows(header, "bob", "a@b.io"))
        self.assertFalse(tokens.allows(header, "bob", "c@d.io"))
        self.assertFalse(tokens.allows(header, "ann", "a@b.io"))

    def test_bad_headers(self):
        for header in (None, "", "Bearer", "Bearer x", "Basic abc"):
            self.assertFalse(tokens.allows(header, "bob", "a@b.io"))

    def test_is_a_jwt(self):
        token = tokens.issue("bob", "a@b.io")
        self.assertEqual(jwt.get_unverified_header(token)["alg"], "HS256")
        claims = jwt.decode(token, options={"verify_signature": False})
        self.assertEqual((claims["sub"], claims["email"], claims["aud"]), ("bob", "a@b.io", "comments.author"))
        self.assertEqual(claims["exp"] - claims["iat"], 3600)

    def test_expires(self):
        with override_settings(AUTHOR_TOKEN_TTL=-1):
            header = f"Bearer {tokens.issue('bob', 'a@b.io')}"
        self.assertFalse(tokens.allows(header, "bob", "a@b.io"))

    def test_forged(self):
        now = int(time.time())
        claims = {"sub": "bob", "email": "a@b.io", "aud": "comments.author", "iat": now, "exp": now + 60}
        forged = (
            jwt.encode(claims, "another key that is long enough for HS256", algorithm="HS256"),
            jwt.encode(claims, None, algorithm="none"),
            jwt.encode({**claims, "aud": "elsewhere"}, tokens._key(), algorithm="HS256"),
            jwt.encode({k: v for k, v in claims.items() if k != "exp"}, tokens._key(), algorithm="HS256"),
        )
        for token in forged:
            self.assertFalse(tokens.allows(f"Bearer {token}", "bob", "a@b.io"))


class ViewportTests(SimpleTestCase):
    valid = {
        "type": "viewport",
        "page": 3,
        "pageSize": 25,
        "ordering": "CREATED_AT_DESC",
        "thread": "123",
        "branches": [{"id": "123", "level": 0}, {"id": "456", "level": 1}],
    }

    def test_valid(self):
        self.assertEqual(parse_viewport(self.valid), ("CREATED_AT_DESC", 25, 3, {123, 456}))

    def test_closed_thread(self):
        message = {**self.valid, "thread": None, "branches": []}
        self.assertEqual(parse_viewport(message), ("CREATED_AT_DESC", 25, 3, set()))

    def test_invalid(self):
        for change in (
            {"type": "other"},
            {"page": 0},
            {"page": "1"},
            {"page": True},
            {"pageSize": 0},
            {"pageSize": 101},
            {"ordering": "ID_DESC"},
            {"ordering": ["CREATED_AT_DESC"]},
            {"thread": "abc"},
            {"branches": None},
            {"branches": ["123"]},
            {"branches": [{"id": "abc", "level": 0}]},
            {"branches": [{"id": "1", "level": -1}]},
            {"branches": [{"id": "1"}]},
            {"branches": [{"id": str(i), "level": 0} for i in range(201)]},
        ):
            with self.subTest(change):
                self.assertIsNone(parse_viewport({**self.valid, **change}))
        self.assertIsNone(parse_viewport([1]))


class ViewersTests(SimpleTestCase):
    def setUp(self):
        shared.client().flushdb()

    async def test_a_new_zone_replaces_the_old_one(self):
        viewers = Viewers("p1")
        await viewers.show("a", "CREATED_AT_DESC", 25, 1)
        await viewers.show("a", "EMAIL_ASC", 10, 2)
        self.assertEqual(await viewers.page_sizes(), {"EMAIL_ASC": {10}})

    async def test_drop(self):
        viewers = Viewers("p1")
        await viewers.show("a", "CREATED_AT_DESC", 25, 1)
        await viewers.show("b", "CREATED_AT_DESC", 10, 1)
        await viewers.show("c", "CREATED_AT_DESC", 10, 2)
        await viewers.drop("a")
        await viewers.drop("b")
        await viewers.drop("unknown")
        self.assertEqual(await viewers.page_sizes(), {"CREATED_AT_DESC": {10}})

    async def test_processes_share_the_tables(self):
        one, two = Viewers("p1"), Viewers("p2")
        await one.show("a", "CREATED_AT_DESC", 25, 1)
        await two.show("b", "CREATED_AT_DESC", 25, 1)
        await two.show("c", "EMAIL_ASC", 10, 1)
        self.assertEqual(await one.page_sizes(), {"CREATED_AT_DESC": {25}, "EMAIL_ASC": {10}})
        await one.drop("a")
        self.assertEqual(await one.page_sizes(), {"CREATED_AT_DESC": {25}, "EMAIL_ASC": {10}})

    async def test_tables_of_a_stopped_process_are_dropped(self):
        alive, stopped = Viewers("alive"), Viewers("stopped")
        await alive.show("a", "CREATED_AT_DESC", 25, 1)
        await stopped.show("b", "CREATED_AT_DESC", 25, 1)
        await stopped.show("c", "EMAIL_ASC", 10, 1)
        await alive.sweep()
        self.assertEqual(await alive.page_sizes(), {"CREATED_AT_DESC": {25}, "EMAIL_ASC": {10}})
        await shared.aclient().delete("viewers:alive:stopped")  # its heartbeats ran out
        with self.assertLogs("comments.live", "INFO"):
            await alive.sweep()
        self.assertEqual(await alive.page_sizes(), {"CREATED_AT_DESC": {25}})
        await alive.drop("a")
        self.assertEqual(await alive.page_sizes(), {})
        self.assertEqual(await shared.aclient().smembers("viewers:processes"), {"alive"})

    async def test_tables_lost_by_redis_come_back_with_the_heartbeat(self):
        viewers = Viewers("p1")
        await viewers.show("a", "CREATED_AT_DESC", 25, 1)
        await viewers.show("b", "EMAIL_ASC", 10, 3)
        await shared.aclient().flushdb()  # Redis restarted
        self.assertEqual(await viewers.page_sizes(), {})
        await viewers.sweep()
        self.assertEqual(await viewers.page_sizes(), {"CREATED_AT_DESC": {25}, "EMAIL_ASC": {10}})
        self.assertEqual(await shared.aclient().hgetall("viewers:of:p1"), {"a": "CREATED_AT_DESC:25:1", "b": "EMAIL_ASC:10:3"})

    async def test_writes_that_failed_are_repeated(self):
        viewers = Viewers("p1")
        await viewers.show("a", "CREATED_AT_DESC", 25, 1)
        await viewers.show("b", "CREATED_AT_DESC", 25, 1)
        with redis_down():
            await viewers.show("a", "EMAIL_ASC", 10, 2)  # as many channels as before
            await viewers.drop("b")
            with self.assertRaises(redis.RedisError):
                await viewers.sweep()
        await viewers.sweep()
        self.assertEqual(await viewers.page_sizes(), {"EMAIL_ASC": {10}})
        self.assertEqual(await shared.aclient().hgetall("viewers:of:p1"), {"a": "EMAIL_ASC:10:2"})


class RedisDownTests(SimpleTestCase):
    async def test_limits_let_requests_through(self):
        with redis_down(), self.assertLogs("comments.ratelimit", "WARNING"):
            shared._warned.clear()
            self.assertTrue(await ratelimit.RateLimiter("test", 0).allow("a"))

    def test_warnings_are_not_repeated_for_a_while(self):
        shared._warned.clear()
        with self.assertLogs("comments.test", "WARNING") as logs:
            for _ in range(3):
                shared.warn(logging.getLogger("comments.test"), "Redis failed", "boom")
        self.assertEqual(len(logs.records), 1)

    def test_clients_give_up_on_a_redis_that_hangs(self):
        options = shared.client().connection_pool.connection_kwargs
        self.assertEqual((options["socket_timeout"], options["socket_connect_timeout"]), (1, 1))

