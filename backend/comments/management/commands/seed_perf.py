import random
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from io import BytesIO

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from PIL import Image, ImageDraw

from comments import storage
from comments.attachments import ProcessedFile
from comments.models import Author, Comment

WORDS = (
    "lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod tempor "
    "incididunt ut labore et dolore magna aliqua enim ad minim veniam quis nostrud "
    "exercitation ullamco laboris nisi aliquip ex ea commodo consequat duis aute irure "
    "in reprehenderit voluptate velit esse cillum fugiat nulla pariatur excepteur sint "
    "occaecat cupidatat non proident sunt culpa qui officia deserunt mollit anim id est"
).split()
NAMES = ["alex", "maria", "ivan", "olga", "john", "anna", "max", "kate", "petr", "lena", "tom", "nina"]
DOMAINS = ["example.com", "mail.test", "inbox.test", "corp.test", "post.test"]
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) Safari/605.1.15",
    "Mozilla/5.0 (Linux; Android 14) Chrome/130.0 Mobile Safari/537.36",
]
# Fixed dates keep the data set identical between runs, not only its shape.
START = datetime(2025, 1, 1, tzinfo=timezone.utc)
SPAN = timedelta(days=365)
# Attachments come from a pool of files shared by many comments, so seeding stays fast.
# Kinds and shares match the k6 upload mix; images are already in their stored 320x240 form.
# The pool keeps the same keys in the bucket, and every seed writes the same bytes over them.
POOL_PREFIX = "perf-seed/"
POOL = {"jpg": 180, "gif": 75, "txt": 45}
CONTENT_TYPES = {"jpg": "image/jpeg", "gif": "image/gif", "txt": "text/plain; charset=utf-8"}
# Bucket requests in parallel: hundreds of them one by one take minutes.
BUCKET_THREADS = 8


@contextmanager
def explicit_created_at():
    """Lets bulk_create keep the generated dates instead of auto_now_add's 'now'."""
    field = Comment._meta.get_field("created_at")
    field.auto_now_add = False
    try:
        yield
    finally:
        field.auto_now_add = True


def in_parallel(function, items):
    with ThreadPoolExecutor(BUCKET_THREADS) as executor:
        return list(executor.map(function, items))


class Command(BaseCommand):
    help = (
        "Replaces ALL comments and authors with a deterministic data set for load tests. "
        "Comment ids are 1..top+replies, the first `top` of them are top-level."
    )

    def add_arguments(self, parser):
        parser.add_argument("--top", type=int, default=10_000, help="top-level comments")
        parser.add_argument("--replies", type=int, default=50_000, help="replies at any depth")
        parser.add_argument("--authors", type=int, default=2_000)
        parser.add_argument("--seed", type=int, default=42)
        parser.add_argument(
            "--attachments", type=float, default=0.1, help="share of comments with an attachment"
        )
        parser.add_argument("--noinput", action="store_true", help="do not ask for confirmation")

    def handle(self, *args, top, replies, authors, seed, attachments, noinput, **options):
        if top < 1 or authors < 1 or replies < 0:
            raise CommandError("--top and --authors must be positive, --replies non-negative.")
        if not 0 <= attachments <= 1:
            raise CommandError("--attachments must be between 0 and 1.")
        if not noinput:
            answer = input("All comments, authors and their attachments will be deleted. Type 'yes': ")
            if answer != "yes":
                raise CommandError("Cancelled.")

        rng = random.Random(seed)
        self.wipe()
        pool = self.make_pool(rng) if attachments else {}
        with transaction.atomic(), explicit_created_at():
            Author.objects.bulk_create(self.make_authors(rng, authors), batch_size=2000)
            comments = self.make_comments(rng, top, replies, authors)
            self.attach(rng, comments, pool, attachments)
            Comment.objects.bulk_create(comments, batch_size=2000)
        self.analyze()

        sizes = Counter(c.root_id for c in comments if c.root_id)
        biggest = max(sizes.values(), default=0)
        attached = sum(1 for c in comments if c.attachment)
        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {authors} authors, {top} top-level comments and {replies} replies "
                f"(ids 1..{top + replies}, biggest thread: {biggest} replies, "
                f"{attached} with attachments from {sum(map(len, pool.values()))} files)."
            )
        )

    def wipe(self):
        # Files uploaded during earlier runs; the pool is written over by make_pool.
        uploaded = (
            Comment.objects.exclude(attachment="")
            .exclude(attachment__startswith=POOL_PREFIX)
            .values_list("attachment", flat=True)
        )
        in_parallel(storage.delete, list(uploaded))
        with transaction.atomic():
            # Nulling the self-references first lets one plain DELETE remove every row
            # instead of Django collecting tens of thousands of objects for cascading.
            Comment.objects.update(parent=None, root=None)
            Comment.objects.all()._raw_delete(Comment.objects.db)
            Author.objects.all()._raw_delete(Author.objects.db)

    def analyze(self):
        # After replacing every row InnoDB refreshes table statistics in the background at
        # an unpredictable moment, and until then the list query can pick a plan twice as
        # slow. Fresh statistics give every run the same plan, the one a live site would have.
        if connection.vendor != "mysql":
            return
        tables = ", ".join(model._meta.db_table for model in (Author, Comment))
        with connection.cursor() as cursor:
            cursor.execute(f"ANALYZE TABLE {tables}")
            cursor.fetchall()

    def make_pool(self, rng):
        # The files are generated in order, so the seed decides their bytes; only the uploads
        # run in parallel.
        files = [
            (kind, f"{POOL_PREFIX}{kind}-{index:03d}.{kind}", self.file(rng, kind))
            for kind, count in POOL.items()
            for index in range(count)
        ]
        in_parallel(
            lambda item: storage.put(
                ProcessedFile(item[2], item[0], CONTENT_TYPES[item[0]], self.kind(item[0])), key=item[1]
            ),
            files,
        )
        pool = {}
        for kind, key, _ in files:
            pool.setdefault(kind, []).append(key)
        return pool

    @staticmethod
    def kind(extension):
        return Comment.AttachmentType.TEXT if extension == "txt" else Comment.AttachmentType.IMAGE

    def file(self, rng, kind):
        if kind == "txt":
            lines = rng.randint(20, 300)
            return "\n".join(" ".join(rng.choice(WORDS) for _ in range(12)) for _ in range(lines)).encode()
        frames = [self.picture(rng) for _ in range(1 if kind == "jpg" else 4)]
        buffer = BytesIO()
        if kind == "jpg":
            frames[0].save(buffer, format="JPEG", quality=90)
        else:
            frames[0].save(buffer, format="GIF", save_all=True, append_images=frames[1:], duration=150, loop=0)
        return buffer.getvalue()

    def picture(self, rng):
        width, height = rng.choice([(320, 240), (240, 320), (320, 180), (200, 200)])
        image = Image.new("RGB", (width, height), tuple(rng.randint(0, 255) for _ in range(3)))
        draw = ImageDraw.Draw(image)
        for _ in range(25):
            x, y = rng.randrange(width), rng.randrange(height)
            size = rng.randint(10, 100)
            draw.ellipse([x, y, x + size, y + size], fill=tuple(rng.randint(0, 255) for _ in range(3)))
        return image

    def attach(self, rng, comments, pool, share):
        if not pool:
            return
        kinds = list(POOL)
        for comment in comments:
            if rng.random() < share:
                kind = rng.choices(kinds, weights=list(POOL.values()))[0]
                comment.attachment = rng.choice(pool[kind])
                comment.attachment_type = self.kind(kind)

    def make_authors(self, rng, count):
        result = []
        for pk in range(1, count + 1):
            username = f"{rng.choice(NAMES)}{pk}"
            result.append(
                Author(
                    pk=pk,
                    username=username,
                    email=f"{username}@{rng.choice(DOMAINS)}",
                    home_page=f"https://{username}.example.com" if rng.random() < 0.3 else "",
                )
            )
        return result

    def make_comments(self, rng, top, replies, authors):
        comments = []
        offsets = sorted(rng.random() for _ in range(top))
        for pk, offset in enumerate(offsets, start=1):
            comments.append(self.comment(rng, pk, authors, START + SPAN * offset))

        # A few threads get most of the replies, as on a real site.
        weights = [min(rng.paretovariate(1.2), 50.0) for _ in range(top)]
        roots = rng.choices(range(1, top + 1), weights=weights, k=replies)
        threads = {}
        for pk, root_id in enumerate(roots, start=top + 1):
            thread = threads.setdefault(root_id, [comments[root_id - 1]])
            parent = thread[0] if rng.random() < 0.5 else rng.choice(thread)
            reply = self.comment(
                rng, pk, authors, parent.created_at + timedelta(minutes=rng.randint(1, 3 * 24 * 60))
            )
            reply.parent_id = parent.pk
            reply.root_id = root_id
            thread.append(reply)
            comments.append(reply)
        return comments

    def comment(self, rng, pk, authors, created_at):
        return Comment(
            pk=pk,
            author_id=rng.randint(1, authors),
            text=self.text(rng),
            ip_address=f"10.{rng.randint(0, 255)}.{rng.randint(0, 255)}.{rng.randint(1, 254)}",
            user_agent=rng.choice(USER_AGENTS),
            created_at=created_at,
        )

    def text(self, rng):
        words = [rng.choice(WORDS) for _ in range(rng.randint(5, 80))]
        # Same markup as real comments, already in the form the sanitizer produces.
        if rng.random() < 0.3:
            index = rng.randrange(len(words))
            tag = rng.choice(["i", "strong", "code"])
            words[index] = f"<{tag}>{words[index]}</{tag}>"
        if rng.random() < 0.05:
            words.append('<a href="https://example.com/page" title="link">link</a>')
        return " ".join(words).capitalize()
