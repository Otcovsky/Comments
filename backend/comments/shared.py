"""Redis, where the state every backend process must see the same way lives: captchas, limits
per IP, who watches what, the count of top-level comments. The DB threads use the blocking
client, the event loop the asyncio one.

Redis may stop or hang, and the callers decide what a failure means (redis.RedisError covers
both): the list of comments works without Redis, the limits per IP let requests through,
captchas cannot be checked, and the viewports are pushed again once it is back.
"""
import asyncio
import time
from functools import cache

import redis
import redis.asyncio
from django.conf import settings
from redis.backoff import NoBackoff
from redis.retry import Retry


def _options(retry):
    return {**settings.REDIS_OPTIONS, "retry": retry, "decode_responses": True}


@cache
def client():
    """The blocking client. Its connection pool is shared by threads."""
    return redis.Redis.from_url(settings.REDIS_URL, **_options(Retry(NoBackoff(), 1)))


_async_clients = {}  # event loop -> its client


def aclient():
    """The asyncio client of the running event loop: its connections belong to that loop.
    A server process has one loop; tests start one per test."""
    loop = asyncio.get_running_loop()
    found = _async_clients.get(loop)
    if found is None:
        for closed in [other for other in _async_clients if other.is_closed()]:
            del _async_clients[closed]
        found = _async_clients[loop] = redis.asyncio.Redis.from_url(
            settings.REDIS_URL, **_options(settings.REDIS_OPTIONS["retry"])
        )
    return found


WARN_EVERY_SECONDS = 10
_warned = {}  # message -> when it was last logged


def warn(logger, message, error):
    """Logs a Redis failure at most once per WARN_EVERY_SECONDS for each message: while Redis
    is down every request fails the same way. Threads may race here and log twice."""
    now = time.monotonic()
    if now - _warned.get(message, -WARN_EVERY_SECONDS) >= WARN_EVERY_SECONDS:
        _warned[message] = now
        logger.warning("%s: %s", message, error)
