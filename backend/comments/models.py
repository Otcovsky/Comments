from django.db import models


class Author(models.Model):
    """A commenter, identified by the pair (username, email)."""

    username = models.CharField(max_length=64)
    email = models.EmailField()
    # Stays as given in the author's first comment: the pair is public, so a later
    # comment must not be able to rewrite the link on all the earlier ones.
    home_page = models.URLField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["username", "email"], name="unique_author"),
        ]
        indexes = [models.Index(fields=["email"])]

    def __str__(self):
        return f"{self.username} <{self.email}>"


class Comment(models.Model):
    class AttachmentType(models.TextChoices):
        NONE = "", "Нет"
        IMAGE = "image", "Изображение"
        TEXT = "text", "Текстовый файл"

    author = models.ForeignKey(Author, on_delete=models.CASCADE, related_name="comments")
    # The author's pair, copied: the list sorts and counts by it, and only columns of this
    # table can share an index with parent_id. The pair of an author never changes.
    username = models.CharField(max_length=64)
    email = models.EmailField()
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.CASCADE, related_name="replies"
    )
    # Top-level comment of the thread, sent to clients as rootId.
    root = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.CASCADE, related_name="thread_comments"
    )
    text = models.TextField(help_text="Sanitized XHTML with allowed tags only")
    # The object's key in the bucket; clients get it under /media/.
    attachment = models.CharField(max_length=100, blank=True)
    attachment_type = models.CharField(
        max_length=10, choices=AttachmentType.choices, blank=True, default=AttachmentType.NONE
    )
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=["root", "created_at"]),
            # Top-level comments in every ordering, ties to the newer id (queries.ORDERINGS),
            # one index per direction: MySQL cannot read one index backwards for both. Without
            # the id a page by date sorted every top-level row: 300 ms at 200,000 of them.
            # The first one also serves the replies of a comment, newest first.
            models.Index(fields=["parent", "-created_at", "-id"], name="comments_top_created_desc"),
            models.Index(fields=["parent", "created_at", "-id"], name="comments_top_created_asc"),
            models.Index(fields=["parent", "username", "-id"], name="comments_top_username_asc"),
            models.Index(fields=["parent", "-username", "-id"], name="comments_top_username_desc"),
            models.Index(fields=["parent", "email", "-id"], name="comments_top_email_asc"),
            models.Index(fields=["parent", "-email", "-id"], name="comments_top_email_desc"),
        ]

    def __str__(self):
        return f"#{self.pk} by {self.author_id}"

    def save(self, *args, **kwargs):
        if not self.username:
            self.username, self.email = self.author.username, self.author.email
        super().save(*args, **kwargs)
