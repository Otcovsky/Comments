"""Who sees what, and which events reach them; see docs/api.md, section 6.

Every open page is a group of the channel layer, so an event for it is one message whatever
the number of processes and sockets. Which pages are open lives in Redis, so that an event
from any backend process counts positions for the tables of all of them. A process that
dies cannot drop its tables, so every process sends heartbeats, and the others drop the
tables of one that stopped.

Each process also keeps its own tables in memory. When Redis lost them (a restart, a failed
write, or others dropping a process whose loop stalled), the next heartbeat writes them again,
so the open pages hear events again within HEARTBEAT_SECONDS of Redis coming back.
"""
import asyncio
import logging
import uuid

import redis
from channels.layers import get_channel_layer

from . import queries, shared
from .pools import in_db_thread
from .validation import parse_id

logger = logging.getLogger(__name__)

MAX_PAGE_SIZE = 100
MAX_BRANCHES = 200
# A process is alive while its heartbeat is younger than PROCESS_TTL_SECONDS. A process whose
# event loop stalls that long loses its tables: its clients hear nothing until they move.
HEARTBEAT_SECONDS = 10
PROCESS_TTL_SECONDS = 60

EVENT_HANDLER = "comment.event"  # CommentsConsumer.comment_event


def branch_group(comment_id):
    return f"branch.{comment_id}"


def page_group(ordering, page_size, page):
    return f"page.{ordering}.{page_size}.{page}"


# The keys, kept consistent by the scripts below:
#   viewers:pages            "<ordering>:<size>:<page>" -> channels showing it
#   viewers:of:<process>     channel -> its table, "<ordering>:<size>:<page>"
#   viewers:alive:<process>  exists while the process sends heartbeats
#   viewers:processes        processes that may have tables
# The scripts build key names from what they read, so they work on a single Redis only, not
# on a cluster.
_DROP = """
local function drop(process, channel)
    local of = 'viewers:of:' .. process
    local tbl = redis.call('HGET', of, channel)
    if not tbl then
        return
    end
    redis.call('HDEL', of, channel)
    if redis.call('HINCRBY', 'viewers:pages', tbl, -1) <= 0 then
        redis.call('HDEL', 'viewers:pages', tbl)
    end
end
"""
SHOW = _DROP + """
local process, channel, tbl, ttl = ARGV[1], ARGV[2], ARGV[3], ARGV[4]
drop(process, channel)
redis.call('HSET', 'viewers:of:' .. process, channel, tbl)
redis.call('HINCRBY', 'viewers:pages', tbl, 1)
redis.call('SADD', 'viewers:processes', process)
redis.call('SET', 'viewers:alive:' .. process, 1, 'EX', ttl)
"""
DROP = _DROP + """
drop(ARGV[1], ARGV[2])
"""
# Replaces every table of this process with the ones it keeps in memory: ARGV[3], ARGV[4] are
# a channel and its table, and so on.
RESYNC = _DROP + """
local process, ttl = ARGV[1], ARGV[2]
local of = 'viewers:of:' .. process
for _, channel in ipairs(redis.call('HKEYS', of)) do
    drop(process, channel)
end
for i = 3, #ARGV, 2 do
    redis.call('HSET', of, ARGV[i], ARGV[i + 1])
    redis.call('HINCRBY', 'viewers:pages', ARGV[i + 1], 1)
end
redis.call('SADD', 'viewers:processes', process)
redis.call('SET', 'viewers:alive:' .. process, 1, 'EX', ttl)
"""
# Drops every table of a process unless it is alive; returns how many.
SWEEP = _DROP + """
local process = ARGV[1]
if redis.call('EXISTS', 'viewers:alive:' .. process) == 1 then
    return 0
end
local of = 'viewers:of:' .. process
local channels = redis.call('HKEYS', of)
for _, channel in ipairs(channels) do
    drop(process, channel)
end
redis.call('DEL', of)
redis.call('SREM', 'viewers:processes', process)
return #channels
"""


class Viewers:
    """Open tables: which channels show which page of which ordering at which size."""

    def __init__(self, process):
        self.process = process
        self._tables = {}  # channel -> table, what Redis must hold for this process
        # Redis may differ from _tables: a write failed. The heartbeat then writes them all.
        self._stale = False
        # The writes of this process go one at a time, so that a resync cannot cross a show or
        # a drop on another connection and bring back a channel that is gone.
        self._locks = {}  # event loop -> its lock
        self._heartbeats = {}  # event loop -> its heartbeat task

    async def show(self, channel, ordering, page_size, page):
        self._keep_alive()
        table = f"{ordering}:{page_size}:{page}"
        async with self._lock():
            self._tables[channel] = table
            await self._write(SHOW, channel, table, PROCESS_TTL_SECONDS)

    async def drop(self, channel):
        async with self._lock():
            if self._tables.pop(channel, None) is not None:
                await self._write(DROP, channel)

    async def open_pages(self):
        """The furthest page open right now, by ordering and page size."""
        pages = {}
        for table in await shared.aclient().hkeys("viewers:pages"):
            ordering, page_size, page = table.rsplit(":", 2)
            furthest = pages.setdefault(ordering, {})
            furthest[int(page_size)] = max(furthest.get(int(page_size), 0), int(page))
        return pages

    async def page_sizes(self):
        """Page sizes open right now, by ordering."""
        return {ordering: set(furthest) for ordering, furthest in (await self.open_pages()).items()}

    async def sweep(self):
        """Says this process is alive, writes its tables again if Redis lost them, and drops
        the tables of processes that are not alive."""
        client = shared.aclient()
        await client.set(f"viewers:alive:{self.process}", 1, ex=PROCESS_TTL_SECONDS)
        async with self._lock():
            if self._stale or await client.hlen(f"viewers:of:{self.process}") != len(self._tables):
                pairs = [value for item in self._tables.items() for value in item]
                await client.eval(RESYNC, 0, self.process, PROCESS_TTL_SECONDS, *pairs)
                self._stale = False
                logger.info("Wrote again %s viewers of this process", len(self._tables))
        dropped = 0
        for process in await client.smembers("viewers:processes"):
            if process != self.process:
                dropped += await client.eval(SWEEP, 0, process)
        if dropped:
            logger.info("Dropped %s viewers of stopped processes", dropped)

    async def _write(self, script, *args):
        """A failed write is not the socket's problem: the heartbeat repeats it later."""
        try:
            await shared.aclient().eval(script, 0, self.process, *args)
        except redis.RedisError as error:
            self._stale = True
            shared.warn(logger, "Viewports wait for Redis", error)

    def _lock(self):
        loop = asyncio.get_running_loop()
        if loop not in self._locks:
            for closed in [other for other in self._locks if other.is_closed()]:
                del self._locks[closed]
            self._locks[loop] = asyncio.Lock()
        return self._locks[loop]

    def _keep_alive(self):
        loop = asyncio.get_running_loop()
        if loop not in self._heartbeats:
            for closed in [other for other in self._heartbeats if other.is_closed()]:
                del self._heartbeats[closed]
            self._heartbeats[loop] = loop.create_task(self._heartbeat())

    async def _heartbeat(self):
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            try:
                await self.sweep()
            except redis.RedisError as error:
                shared.warn(logger, "Viewers heartbeat waits for Redis", error)
            except Exception:
                logger.exception("Viewers heartbeat failed")


viewers = Viewers(uuid.uuid4().hex)


def parse_viewport(message):
    """Returns (ordering, page size, page, branch ids), or None for a message to ignore."""
    if not isinstance(message, dict) or message.get("type") != "viewport":
        return None
    page, page_size = message.get("page"), message.get("pageSize")
    ordering = message.get("ordering")
    thread, branches = message.get("thread"), message.get("branches")
    if type(page) is not int or page < 1:
        return None
    if type(page_size) is not int or not 1 <= page_size <= MAX_PAGE_SIZE:
        return None
    if not isinstance(ordering, str) or ordering not in queries.ORDERINGS:
        return None
    if thread is not None and parse_id(thread) is None:
        return None
    if not isinstance(branches, list) or len(branches) > MAX_BRANCHES:
        return None
    ids = set()
    for branch in branches:
        if not isinstance(branch, dict):
            return None
        comment_id, level = parse_id(branch.get("id")), branch.get("level")
        if comment_id is None or type(level) is not int or level < 0:
            return None
        ids.add(comment_id)
    return ordering, page_size, page, ids


async def _send_to_page(comment_id, payload, newest=False):
    """Sends the event to the tables whose open page shows this top-level comment.

    A comment just created is the newest one, so by date its place needs no counting: it is
    the first, or the last. Comments posted at the same moment may swap places, which only
    matters at a page boundary, and the clients reload the page anyway."""
    pages = await viewers.open_pages()
    if not pages:
        return
    positions = {}
    if newest:
        if "CREATED_AT_DESC" in pages:
            positions["CREATED_AT_DESC"] = 0
        if "CREATED_AT_ASC" in pages:
            positions["CREATED_AT_ASC"] = await in_db_thread(queries.top_level_count.get) - 1
    # Counted up to the end of the furthest open page: a place past it is no open page's,
    # whatever the page size.
    limits = {
        ordering: max(page_size * page for page_size, page in furthest.items())
        for ordering, furthest in pages.items()
        if ordering not in positions
    }
    if limits:
        positions.update(await in_db_thread(queries.positions, comment_id, limits))
    layer = get_channel_layer()
    message = {"type": EVENT_HANDLER, "payload": payload}
    for ordering, furthest in pages.items():
        for page_size in furthest:
            await layer.group_send(page_group(ordering, page_size, positions[ordering] // page_size + 1), message)


async def _send_to_branch(comment_id, payload):
    await get_channel_layer().group_send(branch_group(comment_id), {"type": EVENT_HANDLER, "payload": payload})


async def comment_created(comment, parent):
    """Runs after the comment is committed. A failure here must not fail the request:
    the comment is saved, and clients catch up on their next load."""
    try:
        created = {
            "type": "comment.created",
            "id": str(comment["id"]),
            "parentId": str(parent["id"]) if parent else None,
            "rootId": str(comment["root_id"]) if parent else None,
        }
        if parent is None:
            await _send_to_page(comment["id"], created, newest=True)
            return
        await _send_to_branch(parent["id"], created)
        count = await in_db_thread(queries.reply_count, parent["id"])
        changed = {"type": "comment.replyCount", "id": str(parent["id"]), "replyCount": count}
        if parent["parent_id"] is None:
            await _send_to_page(parent["id"], changed)
        else:
            await _send_to_branch(parent["parent_id"], changed)
    except Exception:
        logger.exception("Could not send events about comment %s", comment["id"])


# Keeps the tasks of comment_created_later alive until they finish.
_dispatching = set()


def comment_created_later(comment, parent):
    """Sends the events about a saved comment without making its author wait: finding out
    who sees the comment costs more than saving it. This process sends them itself: with the
    Celery worker on the same two cores the capacity fell from 192 to 160 it/s
    (perf/results/OPTIMIZATIONS.md, section 7)."""
    task = asyncio.create_task(comment_created(comment, parent))
    _dispatching.add(task)
    task.add_done_callback(_dispatching.discard)


async def finish_dispatching():
    """Waits until the events of the comments saved so far are sent (for tests). A test that
    saves a comment without waiting leaves its task to its own event loop, which cancels it on
    closing and never runs the callback that forgets it: such tasks are skipped."""
    loop = asyncio.get_running_loop()
    while pending := [task for task in _dispatching if task.get_loop() is loop and not task.done()]:
        await asyncio.gather(*pending)
