"""The ASGI application uvicorn serves (config/asgi.py), called the way uvicorn calls it."""
import asyncio
import json
import threading
from unittest import mock

from django.test import TransactionTestCase

from config.asgi import application

LIST = "{ comments(page: 1, pageSize: 1, ordering: CREATED_AT_DESC) { count } }"


async def call(query):
    body = json.dumps({"query": query}).encode()
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/graphql/",
        "raw_path": b"/graphql/",
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"testserver"),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
        "client": ("10.9.0.1", 40000),
        "server": ("testserver", 80),
    }
    requests = [{"type": "http.request", "body": body, "more_body": False}]
    sent = []

    async def receive():
        if requests:
            return requests.pop()
        await asyncio.Event().wait()  # the client never leaves; Django stops listening

    async def send(message):
        sent.append(message)

    await application(scope, receive, send)
    status = next(message["status"] for message in sent if message["type"] == "http.response.start")
    content = b"".join(message.get("body", b"") for message in sent if message["type"] == "http.response.body")
    return status, json.loads(content)


class HandlerTests(TransactionTestCase):
    # Plain methods and a loop of their own, as under uvicorn: an async test runs inside
    # async_to_sync, and then asgiref sends sync calls to the test's thread instead.

    def test_answers(self):
        self.assertEqual(asyncio.run(call(LIST)), (200, {"data": {"comments": {"count": 0}}}))

    def test_a_request_starts_no_thread(self):
        async def three_requests():
            await call(LIST)  # the threads of the process start with the first requests
            await call(LIST)
            start = threading.Thread.start
            with mock.patch.object(threading.Thread, "start", autospec=True, side_effect=start) as started:
                self.assertEqual((await call(LIST))[0], 200)
            return started.call_count

        self.assertEqual(asyncio.run(three_requests()), 0)
