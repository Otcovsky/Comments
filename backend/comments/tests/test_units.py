import ipaddress
from unittest import mock

from django.test import SimpleTestCase, override_settings

from comments import captcha, ratelimit, tokens, validation
from comments.errors import ApiError
from comments.live import Viewers, parse_viewport
from comments.sanitizer import MessageSanitizer, SanitizeError


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
    def test_allows_up_to_the_limit(self):
        limiter = ratelimit.RateLimiter(2)
        self.assertEqual([limiter.allow("a") for _ in range(3)], [True, True, False])
        self.assertTrue(limiter.allow("b"))

    def test_a_new_window_starts_over(self):
        limiter = ratelimit.RateLimiter(1)
        with mock.patch.object(ratelimit.time, "monotonic", return_value=100):
            limiter.allow("a")
            self.assertFalse(limiter.allow("a"))
        with mock.patch.object(ratelimit.time, "monotonic", return_value=100 + ratelimit.WINDOW_SECONDS):
            self.assertTrue(limiter.allow("a"))

    def test_keeps_a_bounded_number_of_addresses(self):
        limiter = ratelimit.RateLimiter(1)
        with mock.patch.object(ratelimit, "MAX_TRACKED", 2):
            for address in "abc":
                limiter.allow(address)
        self.assertEqual(list(limiter._windows), ["b", "c"])

    def test_exempt_network(self):
        with override_settings(RATE_LIMIT_EXEMPT=ipaddress.ip_network("10.0.0.0/24")):
            self.assertTrue(ratelimit.is_exempt("10.0.0.7"))
            self.assertFalse(ratelimit.is_exempt("10.0.1.7"))
            self.assertFalse(ratelimit.is_exempt("not an address"))
            self.assertFalse(ratelimit.is_exempt(None))
        self.assertFalse(ratelimit.is_exempt("10.0.0.7"))


class CaptchaStoreTests(SimpleTestCase):
    def test_answer_ignores_case(self):
        store = captcha.CaptchaStore()
        self.assertTrue(store.check(store.add("AbC12"), "aBc12"))

    def test_is_single_use_even_after_a_wrong_answer(self):
        store = captcha.CaptchaStore()
        key = store.add("AbC12")
        self.assertFalse(store.check(key, "wrong"))
        self.assertFalse(store.check(key, "AbC12"))

    def test_non_ascii_answer_is_just_wrong(self):
        store = captcha.CaptchaStore()
        self.assertFalse(store.check(store.add("AbC12"), "код"))

    def test_expires(self):
        store = captcha.CaptchaStore()
        with mock.patch("comments.captcha.time.monotonic", return_value=1000):
            key = store.add("AbC12")
        with mock.patch("comments.captcha.time.monotonic", return_value=1000 + captcha.TTL_SECONDS):
            self.assertFalse(store.check(key, "AbC12"))

    def test_expired_are_purged(self):
        store = captcha.CaptchaStore()
        with mock.patch("comments.captcha.time.monotonic", return_value=1000):
            store.add("old")
        with mock.patch("comments.captcha.time.monotonic", return_value=1000 + captcha.TTL_SECONDS):
            store.add("new")
        self.assertEqual(len(store._active), 1)

    def test_size_is_bounded(self):
        store = captcha.CaptchaStore()
        with mock.patch("comments.captcha.MAX_ACTIVE", 2):
            first = store.add("a")
            store.add("b")
            store.add("c")
        self.assertEqual(len(store._active), 2)
        self.assertFalse(store.check(first, "a"))

    def test_image_is_a_png_data_url(self):
        self.assertTrue(captcha.render("AbC12").startswith("data:image/png;base64,"))

    @override_settings(CAPTCHA_TEST_ANSWER="PERF1")
    async def test_answer_known_in_a_load_test_and_still_single_use(self):
        key, image = await captcha.issue()
        self.assertTrue(image.startswith("data:image/png;base64,"))
        self.assertTrue(captcha.store.check(key, "PERF1"))
        self.assertFalse(captcha.store.check(key, "PERF1"))


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

    def test_expires(self):
        header = f"Bearer {tokens.issue('bob', 'a@b.io')}"
        with override_settings(AUTHOR_TOKEN_TTL=-1):
            self.assertFalse(tokens.allows(header, "bob", "a@b.io"))


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
    def test_a_new_zone_replaces_the_old_one(self):
        viewers = Viewers()
        viewers.show("a", "CREATED_AT_DESC", 25, 1)
        viewers.show("a", "EMAIL_ASC", 10, 2)
        self.assertEqual(viewers.channels("CREATED_AT_DESC", 25, 1), set())
        self.assertEqual(viewers.channels("EMAIL_ASC", 10, 2), {"a"})
        self.assertEqual(viewers.page_sizes(), {"EMAIL_ASC": {10}})

    def test_drop(self):
        viewers = Viewers()
        viewers.show("a", "CREATED_AT_DESC", 25, 1)
        viewers.show("b", "CREATED_AT_DESC", 10, 1)
        viewers.drop("a")
        viewers.drop("unknown")
        self.assertEqual(viewers.page_sizes(), {"CREATED_AT_DESC": {10}})
