"""Who sees what, and which events reach them; see docs/api.md, section 6.

Only the event loop touches `viewers`, so it needs no locks. It lives in process memory:
after a restart clients reconnect and report their zone again.
"""
import asyncio
import logging
from collections import defaultdict

from channels.exceptions import ChannelFull
from channels.layers import get_channel_layer

from . import queries
from .pools import in_db_thread
from .validation import parse_id

logger = logging.getLogger(__name__)

MAX_PAGE_SIZE = 100
MAX_BRANCHES = 200

EVENT_HANDLER = "comment.event"  # CommentsConsumer.comment_event


def branch_group(comment_id):
    return f"branch.{comment_id}"


class Viewers:
    """Open tables: which channels show which page of which ordering at which size."""

    def __init__(self):
        self._tables = {}  # channel -> (ordering, page size, page)
        self._channels = defaultdict(set)  # (ordering, page size, page) -> channels

    def show(self, channel, ordering, page_size, page):
        self.drop(channel)
        table = (ordering, page_size, page)
        self._tables[channel] = table
        self._channels[table].add(channel)

    def drop(self, channel):
        table = self._tables.pop(channel, None)
        if table is None:
            return
        self._channels[table].discard(channel)
        if not self._channels[table]:
            del self._channels[table]

    def page_sizes(self):
        """Page sizes open right now, by ordering."""
        sizes = defaultdict(set)
        for ordering, page_size, _ in self._channels:
            sizes[ordering].add(page_size)
        return sizes

    def channels(self, ordering, page_size, page):
        return set(self._channels.get((ordering, page_size, page), ()))


viewers = Viewers()


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
    sizes = viewers.page_sizes()
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
    layer = get_channel_layer()
    message = {"type": EVENT_HANDLER, "payload": payload}
    for ordering, page_sizes in sizes.items():
        for page_size in page_sizes:
            for channel in viewers.channels(ordering, page_size, positions[ordering] // page_size + 1):
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
    """Waits until the events of the comments saved so far are sent (for tests)."""
    while _dispatching:
        await asyncio.gather(*_dispatching)
