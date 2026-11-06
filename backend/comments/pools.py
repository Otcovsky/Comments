"""Threads for blocking work: the event loop serves every socket, so it must stay free."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import partial

from django.conf import settings
from django.db import close_old_connections

# Django keeps one connection per thread. A sync view under ASGI gets a new thread for every
# request, so CONN_MAX_AGE keeps nothing and each request opens a connection (measured on this
# database: 1000 connections per 1000 requests, here 8, and three times the throughput).
# These threads live as long as the process, so their connections do too.
_db_threads = ThreadPoolExecutor(settings.DB_THREADS, thread_name_prefix="db")
# Apart from the DB threads, so that slow images cannot hold up reading comments.
_file_threads = ThreadPoolExecutor(settings.FILE_THREADS, thread_name_prefix="file")


def _run_with_fresh_connection(call):
    close_old_connections()
    return call()


async def in_db_thread(function, *args):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_db_threads, _run_with_fresh_connection, partial(function, *args))


async def in_file_thread(function, *args):
    return await asyncio.get_running_loop().run_in_executor(_file_threads, partial(function, *args))
