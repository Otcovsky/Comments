"""Database access. Everything here blocks, so callers go through pools.in_db_thread."""
import logging

import redis
from django.db import connection
from django.db.models import Count, IntegerField, OuterRef, Subquery
from django.db.models.functions import Coalesce

from . import shared
from .models import Author, Comment

logger = logging.getLogger(__name__)

# ordering -> (sort field, descending). Ties always go to the newer id, see docs/api.md.
ORDERINGS = {
    "CREATED_AT_DESC": ("created_at", True),
    "CREATED_AT_ASC": ("created_at", False),
    "USERNAME_ASC": ("username", False),
    "USERNAME_DESC": ("username", True),
    "EMAIL_ASC": ("email", False),
    "EMAIL_DESC": ("email", True),
}

# The index of each ordering, given to MySQL by name. On its own it often reads every
# top-level row through some other index and filters them, which made a position count or
# a deep page 20-40 ms instead of 3-5 (EXPLAIN ANALYZE on 10,757 top-level comments).
INDEXES = {
    "CREATED_AT_DESC": "comments_co_parent__2efacb_idx",
    "CREATED_AT_ASC": "comments_co_parent__2efacb_idx",
    "USERNAME_ASC": "comments_top_username_asc",
    "USERNAME_DESC": "comments_top_username_desc",
    "EMAIL_ASC": "comments_top_email_asc",
    "EMAIL_DESC": "comments_top_email_desc",
}
# Dates have no index with the ties' -id, so a forced index would sort them all; the plan
# MySQL picks for them is already fast.
FORCED_PAGES = ("USERNAME_ASC", "USERNAME_DESC", "EMAIL_ASC", "EMAIL_DESC")

ROW_FIELDS = (
    "id",
    "parent_id",
    "root_id",
    "username",
    "email",
    "author__home_page",
    "text",
    "attachment",
    "attachment_type",
    "created_at",
    "reply_count",
)


def _top_level():
    return Comment.objects.filter(parent__isnull=True)


class TopLevelCount:
    """The number of top-level comments. Every page of the list needs it, and counting 10,000
    index entries costs about as much as the page itself, so it is counted once and forgotten
    when a top-level comment is saved or any comment deleted (signals in apps.py). It lives in
    Redis, so a comment saved by one backend process is counted by all of them.

    A forget moves the generation on, and a count is kept under the generation it started in:
    one that started before a forget lands where nobody reads it.

    Without Redis the list still works: the count comes from the database. A forget that
    fails must not fail the comment already saved (or a migration), so it is only logged; the
    count it missed lives TTL_SECONDS at most."""

    GENERATION_KEY = "top_level_count:generation"
    TTL_SECONDS = 60

    def get(self):
        try:
            return self._cached()
        except redis.RedisError as error:
            shared.warn(logger, "Top-level count from the database, Redis failed", error)
            return _top_level().count()

    def _cached(self):
        r = shared.client()
        key = f"top_level_count:{r.get(self.GENERATION_KEY) or 0}"
        value = r.get(key)
        if value is not None:
            return int(value)
        value = _top_level().count()
        r.set(key, value, ex=self.TTL_SECONDS)
        return value

    def forget(self, **kwargs):
        try:
            shared.client().incr(self.GENERATION_KEY)
        except redis.RedisError as error:
            shared.warn(logger, "Could not forget the top-level count", error)


top_level_count = TopLevelCount()


def _with_reply_count(comments):
    # A subquery, because a JOIN with GROUP BY joins every reply to every comment before LIMIT:
    # that one query once cost 95% of the p95 latency.
    replies = (
        Comment.objects.filter(parent=OuterRef("pk")).order_by().values("parent").annotate(n=Count("pk")).values("n")
    )
    return comments.annotate(reply_count=Coalesce(Subquery(replies), 0, output_field=IntegerField()))


def comment_page(page, page_size, ordering):
    """Returns (count of top-level comments, rows of the page). Past the end there are no rows."""
    count = top_level_count.get()
    offset = (page - 1) * page_size
    if offset >= count:
        return count, []
    field, descending = ORDERINGS[ordering]
    # Two steps: the ids of the page from the index alone, then the rows with their reply
    # counts, so that the count subquery runs for these rows only.
    if ordering in FORCED_PAGES:
        order = f"ORDER BY {field} {'DESC' if descending else 'ASC'}, id DESC LIMIT %s OFFSET %s"
        ids = [row[0] for row in _top_level_sql(ordering, "SELECT id", order, [page_size, offset])]
    else:
        sorted_ids = _top_level().order_by(f"-{field}" if descending else field, "-id").values_list("pk", flat=True)
        ids = list(sorted_ids[offset : offset + page_size])
    rows = _with_reply_count(Comment.objects.filter(pk__in=ids)).values(*ROW_FIELDS)
    by_id = {row["id"]: row for row in rows}
    return count, [by_id[comment_id] for comment_id in ids]


def replies(parent_id):
    rows = _with_reply_count(Comment.objects.filter(parent_id=parent_id)).order_by("-created_at", "-id")
    return list(rows.values(*ROW_FIELDS))


def find_parent(comment_id):
    return Comment.objects.filter(pk=comment_id).values("id", "parent_id", "root_id").first()


def save_comment(*, username, email, home_page, parent, text, attachment, attachment_type, ip_address, user_agent):
    # In autocommit on purpose: inside a REPEATABLE READ transaction a lost get_or_create race
    # could not read the row that beat it. A single INSERT needs no transaction anyway.
    author, _ = Author.objects.get_or_create(
        username=username, email=email, defaults={"home_page": home_page}
    )
    comment = Comment.objects.create(
        author=author,
        parent_id=parent["id"] if parent else None,
        root_id=(parent["root_id"] or parent["id"]) if parent else None,
        text=text,
        attachment=attachment,
        attachment_type=attachment_type,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return {
        "id": comment.id,
        "parent_id": comment.parent_id,
        "root_id": comment.root_id,
        "username": comment.username,
        "email": comment.email,
        "author__home_page": author.home_page,
        "text": comment.text,
        "attachment": comment.attachment,
        "attachment_type": comment.attachment_type,
        "created_at": comment.created_at,
        "reply_count": 0,
    }


def positions(comment_id, orderings):
    """How many top-level comments stand before this one, in each of the orderings."""
    fields = {ORDERINGS[ordering][0] for ordering in orderings}
    values = _top_level().values(*fields).get(pk=comment_id)
    result = {}
    for ordering in orderings:
        field, descending = ORDERINGS[ordering]
        # Two counts and no OR: each is a range of the ordering's index, but for the OR of
        # both MySQL reads every top-level row.
        value = values[field]
        before = _top_level_sql(ordering, "SELECT COUNT(*)", f"AND {field} {'>' if descending else '<'} %s", [value])
        ties = _top_level_sql(ordering, "SELECT COUNT(*)", f"AND {field} = %s AND id > %s", [value, comment_id])
        result[ordering] = before[0][0] + ties[0][0]
    return result


def _top_level_sql(ordering, select, rest, params):
    """A query over the top-level comments through the index of the ordering. Only names
    from ORDERINGS and INDEXES go into the SQL; values are parameters."""
    sql = f"{select} FROM {Comment._meta.db_table} FORCE INDEX ({INDEXES[ordering]}) WHERE parent_id IS NULL {rest}"
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchall()


def reply_count(comment_id):
    return Comment.objects.filter(parent_id=comment_id).count()
