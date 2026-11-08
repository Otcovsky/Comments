"""Reads of comments cached in Redis. The cache only answers what the database would: a write
that makes an answer stale moves on the version it is kept under, and a reader reads the
version before the database, so an answer it found before the write lands where nobody reads.

The keys; <e> is the epoch, which a flush of the database (post_migrate) moves on, so that a
new database never meets rows of the old one with the same ids:
    cache:epoch
    top_level_count:generation           moved on by a new top-level comment or any delete
    top_level_count:<g>                  the count of top-level comments in generation <g>
    <e>:page:<g>:<ordering>:<size>:<n>   ids of a page of top-level comments, in generation <g>
    <e>:comment:<id>                     a row without its reply count: comments do not change
    <e>:children:v:<id>                  moved on by a new direct reply to <id> or its deletion
    <e>:children:<id>:<v>                ids of the direct replies, newest first, in version <v>
    <e>:replycount:<id>:<v>              how many there are, in version <v>

Everything here blocks: the DB threads call it. It raises redis.RedisError, and the callers in
queries.py then read the database.
"""
import json
import logging
from datetime import datetime

import redis

from . import shared

logger = logging.getLogger(__name__)

GENERATION_KEY = "top_level_count:generation"
EPOCH_KEY = "cache:epoch"
# What a missed forget (Redis failed right then) can serve stale at most.
TTL_SECONDS = 60
# Rows never change; this only lets old epochs go.
ROW_TTL_SECONDS = 24 * 3600
# A version outlives every value kept under it, or a lost version could bring one back.
VERSION_TTL_SECONDS = 24 * 3600


def count_key(generation):
    return f"top_level_count:{generation}"


_PAGE = """
local epoch = redis.call('GET', KEYS[1]) or '0'
local generation = redis.call('GET', KEYS[2]) or '0'
local count = redis.call('GET', 'top_level_count:' .. generation)
local ids = redis.call('GET', epoch .. ':page:' .. generation .. ':' .. ARGV[1])
return {epoch, generation, count or false, ids or false}
"""
_ROWS = """
local epoch = redis.call('GET', KEYS[1]) or '0'
local result = {epoch}
for i = 1, #ARGV do
    local version = redis.call('GET', epoch .. ':children:v:' .. ARGV[i]) or '0'
    result[#result + 1] = version
    result[#result + 1] = redis.call('GET', epoch .. ':comment:' .. ARGV[i]) or false
    result[#result + 1] = redis.call('GET', epoch .. ':replycount:' .. ARGV[i] .. ':' .. version) or false
end
return result
"""
_CHILDREN = """
local epoch = redis.call('GET', KEYS[1]) or '0'
local version = redis.call('GET', epoch .. ':children:v:' .. ARGV[1]) or '0'
return {epoch, version, redis.call('GET', epoch .. ':children:' .. ARGV[1] .. ':' .. version) or false}
"""
# ARGV: the comment, whether its row goes too (deleted), the lifetime of its version.
_FORGET = """
local epoch = redis.call('GET', KEYS[1]) or '0'
local version = epoch .. ':children:v:' .. ARGV[1]
redis.call('INCR', version)
redis.call('EXPIRE', version, ARGV[3])
if ARGV[2] == '1' then
    redis.call('DEL', epoch .. ':comment:' .. ARGV[1])
end
"""


def page(ordering, page_size, number):
    """Returns (epoch, generation, count or None, ids or None)."""
    epoch, generation, count, ids = shared.client().eval(
        _PAGE, 2, EPOCH_KEY, GENERATION_KEY, f"{ordering}:{page_size}:{number}"
    )
    return epoch, generation, None if count is None else int(count), None if ids is None else json.loads(ids)


def store_page(epoch, generation, ordering, page_size, number, count=None, ids=None):
    with shared.client().pipeline(transaction=False) as pipe:
        if count is not None:
            pipe.set(count_key(generation), count, ex=TTL_SECONDS)
        if ids is not None:
            pipe.set(f"{epoch}:page:{generation}:{ordering}:{page_size}:{number}", json.dumps(ids), ex=TTL_SECONDS)
        pipe.execute()


def rows(ids):
    """Returns (epoch, version of each id, rows found whole: id -> row with its reply count)."""
    epoch, *found = shared.client().eval(_ROWS, 1, EPOCH_KEY, *ids)
    versions, whole = {}, {}
    for comment_id, (version, row, count) in zip(ids, zip(found[0::3], found[1::3], found[2::3])):
        versions[comment_id] = version
        if row is not None and count is not None:
            whole[comment_id] = {**_load_row(row), "reply_count": int(count)}
    return epoch, versions, whole


def store_rows(epoch, versions, found):
    """Keeps rows read from the database, each count under the version read before them."""
    with shared.client().pipeline(transaction=False) as pipe:
        for row in found:
            comment_id = row["id"]
            pipe.set(f"{epoch}:comment:{comment_id}", _dump_row(row), ex=ROW_TTL_SECONDS)
            pipe.set(f"{epoch}:replycount:{comment_id}:{versions[comment_id]}", row["reply_count"], ex=TTL_SECONDS)
        pipe.execute()


def children(parent_id):
    """Returns (epoch, version, ids of the direct replies or None)."""
    epoch, version, ids = shared.client().eval(_CHILDREN, 1, EPOCH_KEY, parent_id)
    return epoch, version, None if ids is None else json.loads(ids)


def store_children(epoch, version, parent_id, ids):
    shared.client().set(f"{epoch}:children:{parent_id}:{version}", json.dumps(ids), ex=TTL_SECONDS)


# The writes below run from the signals of a comment already saved or deleted (apps.py): a
# failure must not fail it, so it is logged, and what it missed lives TTL_SECONDS at most.


def forget_replies(parent_id):
    """A direct reply to parent_id was saved or deleted."""
    _forget(parent_id, deleted=False)


def forget_comment(comment_id):
    """The comment was deleted."""
    _forget(comment_id, deleted=True)


def forget_all(**kwargs):
    """The database was flushed or migrated."""
    try:
        shared.client().incr(EPOCH_KEY)
    except redis.RedisError as error:
        shared.warn(logger, "Could not forget the cached comments", error)


def _forget(comment_id, deleted):
    try:
        shared.client().eval(_FORGET, 1, EPOCH_KEY, comment_id, int(deleted), VERSION_TTL_SECONDS)
    except redis.RedisError as error:
        shared.warn(logger, "Could not forget cached replies", error)


def _dump_row(row):
    row = {key: value for key, value in row.items() if key != "reply_count"}
    return json.dumps({**row, "created_at": row["created_at"].isoformat()})


def _load_row(text):
    row = json.loads(text)
    return {**row, "created_at": datetime.fromisoformat(row["created_at"])}
