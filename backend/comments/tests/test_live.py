"""The table of examples from docs/api.md, section 6, and the rest of the socket's rules."""
import json
from unittest import mock

from channels.testing import WebsocketCommunicator
from django.test import TransactionTestCase

from comments import live, queries
from comments.live import viewers
from comments.routing import websocket_urlpatterns

from .helpers import post, redis_down

application = websocket_urlpatterns[0].callback
QUIET = 0.1


def viewport(page=1, page_size=25, ordering="CREATED_AT_DESC", thread=None, branches=()):
    return {
        "type": "viewport",
        "page": page,
        "pageSize": page_size,
        "ordering": ordering,
        "thread": thread,
        "branches": [{"id": str(branch), "level": level} for level, branch in enumerate(branches)],
    }


class SocketTests(TransactionTestCase):
    async def connect(self, *args, **options):
        socket = WebsocketCommunicator(application, "/ws/comments/")
        connected, _ = await socket.connect()
        self.assertTrue(connected)
        self.sockets.append(socket)
        await self.show(socket, *args, **options)
        return socket

    async def show(self, socket, *args, **options):
        await socket.send_json_to(viewport(*args, **options))
        await socket.receive_nothing(QUIET)  # the consumer has handled it by now

    def setUp(self):
        self.sockets = []

    async def close(self):
        for socket in self.sockets:
            await socket.disconnect()

    async def test_first_page_hears_about_a_new_comment(self):
        socket = await self.connect()
        comment = await post()
        self.assertEqual(
            await socket.receive_json_from(),
            {"type": "comment.created", "id": comment["id"], "parentId": None, "rootId": None},
        )
        await self.close()

    async def test_new_comment_by_date_needs_no_position_count(self):
        for _ in range(3):
            await post()
        newest = await self.connect()
        last_page = await self.connect(page=2, page_size=2, ordering="CREATED_AT_ASC")
        with mock.patch.object(queries, "positions", side_effect=AssertionError("counted")):
            comment = await post()
        for socket in (newest, last_page):
            self.assertEqual((await socket.receive_json_from())["id"], comment["id"])
        await self.close()

    async def test_every_socket_of_a_page_hears(self):
        first, second = await self.connect(), await self.connect()
        comment = await post()
        for socket in (first, second):
            self.assertEqual((await socket.receive_json_from())["id"], comment["id"])
            self.assertTrue(await socket.receive_nothing(QUIET))
        await self.close()

    async def test_third_page_hears_nothing(self):
        for _ in range(4):
            await post()
        socket = await self.connect(page=3, page_size=2)
        await post()
        self.assertTrue(await socket.receive_nothing(QUIET))
        await self.close()

    async def test_reply_to_a_row_updates_its_count(self):
        root = await post()
        socket = await self.connect()
        await post(parent_id=root["id"])
        self.assertEqual(
            await socket.receive_json_from(),
            {"type": "comment.replyCount", "id": root["id"], "replyCount": 1},
        )
        self.assertTrue(await socket.receive_nothing(QUIET))
        await self.close()

    async def test_open_branch_hears_about_a_reply(self):
        root = await post()
        socket = await self.connect(thread=root["id"], branches=[root["id"]])
        reply = await post(parent_id=root["id"])
        self.assertEqual(
            await socket.receive_json_from(),
            {"type": "comment.created", "id": reply["id"], "parentId": root["id"], "rootId": root["id"]},
        )
        # The root's row is on the page too, so its counter moves as well.
        self.assertEqual(
            await socket.receive_json_from(),
            {"type": "comment.replyCount", "id": root["id"], "replyCount": 1},
        )
        await self.close()

    async def test_reply_to_a_visible_closed_comment_updates_its_count(self):
        root = await post()
        reply = await post(parent_id=root["id"])
        socket = await self.connect(thread=root["id"], branches=[root["id"]])
        await post(parent_id=reply["id"])
        self.assertEqual(
            await socket.receive_json_from(),
            {"type": "comment.replyCount", "id": reply["id"], "replyCount": 1},
        )
        await self.close()

    async def test_open_nested_branch_hears_about_a_reply(self):
        root = await post()
        reply = await post(parent_id=root["id"])
        socket = await self.connect(thread=root["id"], branches=[root["id"], reply["id"]])
        deep = await post(parent_id=reply["id"])
        self.assertEqual(
            await socket.receive_json_from(),
            {"type": "comment.created", "id": deep["id"], "parentId": reply["id"], "rootId": root["id"]},
        )
        self.assertEqual(
            await socket.receive_json_from(),
            {"type": "comment.replyCount", "id": reply["id"], "replyCount": 1},
        )
        await self.close()

    async def test_closed_branch_hears_nothing(self):
        root = await post()
        reply = await post(parent_id=root["id"])
        socket = await self.connect()
        await post(parent_id=reply["id"])
        self.assertTrue(await socket.receive_nothing(QUIET))
        await self.close()

    async def test_page_depends_on_ordering_and_size(self):
        for name in ("ann", "bob", "cid"):
            await post(name, f"{name}@example.com")
        by_name = await self.connect(page=2, page_size=1, ordering="USERNAME_ASC")
        other_size = await self.connect(page=2, page_size=2, ordering="USERNAME_ASC")
        by_date = await self.connect(page=1, page_size=1, ordering="CREATED_AT_ASC")
        await post("azz", "azz@example.com")  # ann, azz, bob, cid
        self.assertEqual((await by_name.receive_json_from())["type"], "comment.created")
        self.assertTrue(await other_size.receive_nothing(QUIET))
        self.assertTrue(await by_date.receive_nothing(QUIET))
        await self.close()

    async def test_a_new_message_replaces_the_zone(self):
        root = await post()
        socket = await self.connect(thread=root["id"], branches=[root["id"]])
        await self.show(socket, page=5)
        await post(parent_id=root["id"])
        await post()
        self.assertTrue(await socket.receive_nothing(QUIET))
        await self.close()

    async def test_bad_messages_leave_the_zone_in_force(self):
        socket = await self.connect()
        await socket.send_to(text_data="not json")
        await socket.send_to(text_data=json.dumps({"type": "viewport", "page": 0}))
        await socket.send_to(text_data=json.dumps([1, 2]))
        await socket.send_to(text_data="x" * 40000)
        await socket.send_to(text_data="[" * 30000)  # deep enough for a RecursionError
        await socket.send_to(bytes_data=b"\x00")
        await socket.receive_nothing(QUIET)
        await post()
        self.assertEqual((await socket.receive_json_from())["type"], "comment.created")
        await self.close()

    async def test_disconnect_forgets_the_viewer(self):
        socket = await self.connect()
        await socket.disconnect()
        self.sockets.clear()
        self.assertEqual(await viewers.page_sizes(), {})
        await post()  # nobody watches, and nothing breaks

    async def test_a_zone_sent_while_redis_is_down_is_heard_once_it_is_back(self):
        socket = await self.connect(page=5)
        with redis_down():
            await self.show(socket)  # the first page now, written nowhere
        await viewers.sweep()  # the next heartbeat
        comment = await post()
        self.assertEqual((await socket.receive_json_from())["id"], comment["id"])
        await self.close()

    async def test_broken_events_do_not_fail_the_comment(self):
        socket = await self.connect()

        async def broken(*args):
            raise RuntimeError("boom")

        original, live._send_to_page = live._send_to_page, broken
        try:
            with self.assertLogs("comments.live", "ERROR"):
                comment = await post()
        finally:
            live._send_to_page = original
        self.assertTrue(comment["id"])
        await self.close()
