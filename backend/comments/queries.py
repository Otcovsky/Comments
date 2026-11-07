"""Database access. Everything here blocks, so callers go through pools.in_db_thread."""
import threading

from django.db.models import Count, IntegerField, OuterRef, Q, Subquery
from django.db.models.functions import Coalesce

from .models import Author, Comment

# ordering -> (sort field, descending). Ties always go to the newer id, see docs/api.md.
ORDERINGS = {
    "CREATED_AT_DESC": ("created_at", True),
    "CREATED_AT_ASC": ("created_at", False),
    "USERNAME_ASC": ("author__username", False),
    "USERNAME_DESC": ("author__username", True),
    "EMAIL_ASC": ("author__email", False),
    "EMAIL_DESC": ("author__email", True),
}

ROW_FIELDS = (
    "id",
    "parent_id",
    "root_id",
    "author__username",
    "author__email",
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
    when a top-level comment is saved or any comment deleted (signals in apps.py). The DB
    threads share it, hence the lock."""

    def __init__(self):
        self._lock = threading.Lock()
        self._value = None
        # Moves on with every forget, so a count that started before it is not kept.
        self._generation = 0

    def get(self):
        with self._lock:
            if self._value is not None:
                return self._value
            generation = self._generation
        value = _top_level().count()
        with self._lock:
            if generation == self._generation:
                self._value = value
        return value

    def forget(self, **kwargs):
        with self._lock:
            self._value = None
            self._generation += 1


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
    # Two steps: when the sort needs a filesort (username, email), MySQL would otherwise run
    # the reply count subquery for all the top-level comments before applying LIMIT.
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
        "author__username": author.username,
        "author__email": author.email,
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
        earlier = Q(**{f"{field}__{'gt' if descending else 'lt'}": values[field]})
        earlier |= Q(**{field: values[field], "pk__gt": comment_id})
        result[ordering] = _top_level().filter(earlier).count()
    return result


def reply_count(comment_id):
    return Comment.objects.filter(parent_id=comment_id).count()
