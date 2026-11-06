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
            models.Index(fields=["parent", "-created_at"]),
            models.Index(fields=["root", "created_at"]),
        ]

    def __str__(self):
        return f"#{self.pk} by {self.author_id}"
