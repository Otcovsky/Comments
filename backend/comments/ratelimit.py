"""Limits per IP for the operations that cost the most. Nginx limits all requests, but it
cannot see which GraphQL operation a request carries.

The counters live in Redis, so that the limit holds for all backend processes together,
and expire with their window.
"""
import ipaddress

from django.conf import settings

from . import shared

WINDOW_SECONDS = 60
COMMENTS_PER_WINDOW = 10
CAPTCHAS_PER_WINDOW = 30


class RateLimiter:
    def __init__(self, name, limit):
        self.name = name
        self.limit = limit

    async def allow(self, address):
        """A window starts with the first request from the address and lasts WINDOW_SECONDS."""
        key = f"rate:{self.name}:{address}"
        async with shared.aclient().pipeline(transaction=True) as pipe:
            pipe.set(key, 0, ex=WINDOW_SECONDS, nx=True)
            pipe.incr(key)
            _, requests = await pipe.execute()
        return requests <= self.limit


def is_exempt(address):
    """Only the load machine of a load test (settings.RATE_LIMIT_EXEMPT)."""
    network = settings.RATE_LIMIT_EXEMPT
    if network is None or not address:
        return False
    try:
        return ipaddress.ip_address(address) in network
    except ValueError:
        return False


comments = RateLimiter("comments", COMMENTS_PER_WINDOW)
captchas = RateLimiter("captchas", CAPTCHAS_PER_WINDOW)
