"""Redis, where the state every backend process must see the same way lives: captchas, limits
per IP, who watches what, the count of top-level comments. The DB threads use the blocking
client, the event loop the asyncio one.
"""
import asyncio
from functools import cache

import redis
import redis.asyncio
from django.conf import settings


@cache
def client():
    """The blocking client. Its connection pool is shared by threads."""
    return redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)


_async_clients = {}  # event loop -> its client


def aclient():
    """The asyncio client of the running event loop: its connections belong to that loop.
    A server process has one loop; tests start one per test."""
    loop = asyncio.get_running_loop()
    found = _async_clients.get(loop)
    if found is None:
        for closed in [other for other in _async_clients if other.is_closed()]:
            del _async_clients[closed]
        found = _async_clients[loop] = redis.asyncio.Redis.from_url(settings.REDIS_URL, decode_responses=True)
    return found
