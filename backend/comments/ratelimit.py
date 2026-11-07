"""Limits per IP for the operations that cost the most. Nginx limits all requests, but it
cannot see which GraphQL operation a request carries.

Only the event loop touches the counters, so they need no locks. They live in process
memory: a restart only clears them.
"""
import ipaddress
import time
from collections import OrderedDict

from django.conf import settings

WINDOW_SECONDS = 60
COMMENTS_PER_WINDOW = 10
CAPTCHAS_PER_WINDOW = 30
# Enough for a burst; past it the oldest counters are forgotten.
MAX_TRACKED = 100_000


class RateLimiter:
    def __init__(self, limit):
        self.limit = limit
        self._windows = OrderedDict()  # address -> [requests, window end], earliest end first

    def allow(self, address):
        now = time.monotonic()
        while self._windows and (
            len(self._windows) >= MAX_TRACKED or next(iter(self._windows.values()))[1] <= now
        ):
            self._windows.popitem(last=False)
        window = self._windows.get(address)
        if window is None:
            self._windows[address] = [1, now + WINDOW_SECONDS]
            return True
        window[0] += 1
        return window[0] <= self.limit


def is_exempt(address):
    """Only the load machine of a load test (settings.RATE_LIMIT_EXEMPT)."""
    network = settings.RATE_LIMIT_EXEMPT
    if network is None or not address:
        return False
    try:
        return ipaddress.ip_address(address) in network
    except ValueError:
        return False


comments = RateLimiter(COMMENTS_PER_WINDOW)
captchas = RateLimiter(CAPTCHAS_PER_WINDOW)
