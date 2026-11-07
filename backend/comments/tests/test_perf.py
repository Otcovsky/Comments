from unittest import mock

from django.core.management import call_command
from django.test import TransactionTestCase

from comments.management.commands import seed_perf
from comments.models import Author, Comment

from .helpers import make_author, make_comment


class SeedTests(TransactionTestCase):
    def seed(self, client, **options):
        with mock.patch("comments.storage._client", return_value=client):
            call_command("seed_perf", top=5, replies=20, authors=3, noinput=True, stdout=mock.Mock(), **options)

    def test_replaces_everything_with_the_same_data_set(self):
        old = make_comment(make_author("old"), attachment="attachments/2026/11/old.jpg", attachment_type="image")
        client = mock.Mock()
        self.seed(client, attachments=0.5)
        client.delete_object.assert_called_once_with(Bucket=mock.ANY, Key="attachments/2026/11/old.jpg")
        self.assertFalse(Comment.objects.filter(pk=old.pk).exists())
        self.assertEqual(Author.objects.count(), 3)
        self.assertEqual(sorted(Comment.objects.values_list("pk", flat=True)), list(range(1, 26)))
        self.assertEqual(
            sorted(Comment.objects.filter(parent__isnull=True).values_list("pk", flat=True)), [1, 2, 3, 4, 5]
        )
        for reply in Comment.objects.filter(parent__isnull=False):
            self.assertEqual(reply.root_id, reply.parent.root_id or reply.parent_id)
            self.assertGreater(reply.created_at, reply.parent.created_at)
        keys = {call.kwargs["Key"] for call in client.put_object.call_args_list}
        self.assertEqual(len(keys), sum(seed_perf.POOL.values()))
        attached = Comment.objects.exclude(attachment="")
        self.assertTrue(attached.exists())
        for comment in attached:
            self.assertIn(comment.attachment, keys)
            self.assertEqual(comment.attachment_type, "text" if comment.attachment.endswith(".txt") else "image")

        first = list(Comment.objects.order_by("pk").values("text", "parent_id", "attachment", "created_at"))
        self.seed(client, attachments=0.5)
        # Pool files are written over, never deleted.
        client.delete_object.assert_called_once()
        self.assertEqual(list(Comment.objects.order_by("pk").values("text", "parent_id", "attachment", "created_at")), first)
