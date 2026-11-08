"""Who sees what, and which events reach them; see docs/api.md, section 6.

Who sees what lives in Redis, so that an event from any backend process finds the tables of
all of them. A process that dies cannot drop its tables, so every process sends heartbeats,
and the others drop the tables of one that stopped.
"""
import asyncio
import logging
import uuid

from channels.exceptions import ChannelFull
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


# The keys, kept consistent by the scripts below:
#   viewers:table:<ordering>:<size>:<page>  channels showing that page
#   viewers:sizes                           "<ordering>:<size>" -> channels showing a page of it
#   viewers:of:<process>                    channel -> its table, "<ordering>:<size>:<page>"
#   viewers:alive:<process>                 exists while the process sends heartbeats
#   viewers:processes                       processes that may have tables
# The keys of a table are found from what the scripts read, so they work on a single Redis
# only, not on a cluster.
_DROP = """
local function drop(process, channel)
    local of = 'viewers:of:' .. process
    local tbl = redis.call('HGET', of, channel)
    if not tbl then
        return
    end
    redis.call('HDEL', of, channel)
    redis.call('SREM', 'viewers:table:' .. tbl, channel)
    local size = string.match(tbl, '^(.+):%d+$')
    if redis.call('HINCRBY', 'viewers:sizes', size, -1) <= 0 then
        redis.call('HDEL', 'viewers:sizes', size)
    end
end
"""
SHOW = _DROP + """
local process, channel, tbl, ttl = ARGV[1], ARGV[2], ARGV[3], ARGV[4]
drop(process, channel)
redis.call('HSET', 'viewers:of:' .. process, channel, tbl)
redis.call('SADD', 'viewers:table:' .. tbl, channel)
redis.call('HINCRBY', 'viewers:sizes', string.match(tbl, '^(.+):%d+$'), 1)
redis.call('SADD', 'viewers:processes', process)
redis.call('SET', 'viewers:alive:' .. process, 1, 'EX', ttl)
"""
DROP = _DROP + """
drop(ARGV[1], ARGV[2])
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
        self._heartbeats = {}  # event loop -> its heartbeat task

    async def show(self, channel, ordering, page_size, page):
        self._keep_alive()
        table = f"{ordering}:{page_size}:{page}"
        await self._run(SHOW, channel, table, PROCESS_TTL_SECONDS)

    async def drop(self, channel):
        await self._run(DROP, channel)

    async def page_sizes(self):
        """Page sizes open right now, by ordering."""
        sizes = {}
        for size in await shared.aclient().hkeys("viewers:sizes"):
            ordering, page_size = size.rsplit(":", 1)
            sizes.setdefault(ordering, set()).add(int(page_size))
        return sizes

    async def channels(self, tables):
        """The channels of each (ordering, page size, page), in one round trip."""
        async with shared.aclient().pipeline(transaction=False) as pipe:
            for ordering, page_size, page in tables:
                pipe.smembers(f"viewers:table:{ordering}:{page_size}:{page}")
            return await pipe.execute()

    async def sweep(self):
        """Says this process is alive and drops the tables of processes that are not."""
        redis = shared.aclient()
        await redis.set(f"viewers:alive:{self.process}", 1, ex=PROCESS_TTL_SECONDS)
        dropped = 0
        for process in await redis.smembers("viewers:processes"):
            if process != self.process:
                dropped += await redis.eval(SWEEP, 0, process)
        if dropped:
            logger.info("Dropped %s viewers of stopped processes", dropped)

    async def _run(self, script, *args):
        await shared.aclient().eval(script, 0, self.process, *args)

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
    sizes = await viewers.page_sizes()
    if not sizes:
        return
    positions = {}
    if newest:
        if "CREATED_AT_DESC" in sizes:
            positions["CREATED_AT_DESC"] = 0
        if "CREATED_AT_ASC" in sizes:
            positions["CREATED_AT_ASC"] = await in_db_thread(queries.top_level_count.get) - 1
    counted = [ordering for ordering in sizes if ordering not in positions]
    if counted:
        positions.update(await in_db_thread(queries.positions, comment_id, counted))
    tables = [
        (ordering, page_size, positions[ordering] // page_size + 1)
        for ordering, page_sizes in sizes.items()
        for page_size in page_sizes
    ]
    layer = get_channel_layer()
    message = {"type": EVENT_HANDLER, "payload": payload}
    for channels in await viewers.channels(tables):
        for channel in channels:
            try:
                await layer.send(channel, message)
            except ChannelFull:
                # A client too slow to read loses this event, as after a dropped connection.
                logger.warning("Channel is full, event dropped")


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
    who sees the comment costs more than saving it."""
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
