"""Threads for blocking work: the event loop serves every socket, so it must stay free."""
import asyncio
import logging
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from functools import partial

from django.conf import settings
from django.db import close_old_connections

logger = logging.getLogger(__name__)

# Django keeps one connection per thread. A sync view under ASGI gets a new thread for every
# request, so CONN_MAX_AGE keeps nothing and each request opens a connection (measured on this
# database: 1000 connections per 1000 requests, here 8, and three times the throughput).
# These threads live as long as the process, so their connections do too.
_db_threads = ThreadPoolExecutor(settings.DB_THREADS, thread_name_prefix="db")
# Apart from the DB threads, so that slow images cannot hold up reading comments.
_file_threads = ThreadPoolExecutor(settings.FILE_THREADS, thread_name_prefix="file")


def _percentile(values, share):
    return values[min(len(values) - 1, int(len(values) * share))]


class PoolStats:
    """How long calls wait for a free DB thread and how long they then run, by function.
    Only for load tests (settings.DB_POOL_REPORT): it tells a queue for the threads from a
    slow database. Calls are counted from the DB threads and the event loop, hence the lock."""

    def __init__(self):
        self._lock = threading.Lock()
        self._calls = defaultdict(list)  # function name -> [(wait, run)], seconds
        self._queued = self._busy = 0
        self._queued_max = self._busy_max = 0

    def submitted(self):
        with self._lock:
            self._queued += 1
            self._queued_max = max(self._queued_max, self._queued)

    def started(self):
        with self._lock:
            self._queued -= 1
            self._busy += 1
            self._busy_max = max(self._busy_max, self._busy)

    def finished(self, name, wait, run):
        with self._lock:
            self._busy -= 1
            self._calls[name].append((wait, run))

    def report(self):
        """One log line about the calls since the last report, or None if there were none."""
        with self._lock:
            calls, self._calls = self._calls, defaultdict(list)
            queued_max, busy_max = self._queued_max, self._busy_max
            self._queued_max, self._busy_max = self._queued, self._busy
        if not calls:
            return None
        parts = [f"db pool: busy max {busy_max}/{settings.DB_THREADS}, queued max {queued_max}"]
        for name, timings in sorted(calls.items(), key=lambda item: -sum(run for _, run in item[1])):
            waits = sorted(wait * 1000 for wait, _ in timings)
            runs = sorted(run * 1000 for _, run in timings)
            parts.append(
                f"{name} n={len(timings)} wait p50/p95/max {_percentile(waits, 0.5):.1f}/"
                f"{_percentile(waits, 0.95):.1f}/{waits[-1]:.1f} ms run p50/p95 "
                f"{_percentile(runs, 0.5):.1f}/{_percentile(runs, 0.95):.1f} ms"
            )
        return "; ".join(parts)


stats = PoolStats()
_reporter = None


async def _report_every(seconds):
    while True:
        await asyncio.sleep(seconds)
        line = stats.report()
        if line:
            logger.info(line)


def _run_with_fresh_connection(call, submitted=None):
    if submitted is None:
        close_old_connections()
        return call()
    started = time.perf_counter()
    stats.started()
    try:
        close_old_connections()
        return call()
    finally:
        name = getattr(call.func, "__qualname__", "call")
        stats.finished(name, started - submitted, time.perf_counter() - started)


async def in_db_thread(function, *args, **kwargs):
    global _reporter
    loop = asyncio.get_running_loop()
    call = partial(function, *args, **kwargs)
    if not settings.DB_POOL_REPORT:
        return await loop.run_in_executor(_db_threads, _run_with_fresh_connection, call)
    if _reporter is None or _reporter.done():
        _reporter = loop.create_task(_report_every(settings.DB_POOL_REPORT))
    stats.submitted()
    return await loop.run_in_executor(_db_threads, _run_with_fresh_connection, call, time.perf_counter())


async def in_file_thread(function, *args):
    return await asyncio.get_running_loop().run_in_executor(_file_threads, partial(function, *args))
