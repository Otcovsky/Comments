from django.db import migrations, models


class Migration(migrations.Migration):
    """Indexes by date that hold the ties' id too: with 200,000 top-level comments a page by
    date sorted all of them instead of reading the index. The new indexes come first, since
    the foreign key on parent_id needs an index that starts with it at every moment."""

    dependencies = [
        ('comments', '0005_comment_author_copy'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='comment',
            index=models.Index(fields=['parent', '-created_at', '-id'], name='comments_top_created_desc'),
        ),
        migrations.AddIndex(
            model_name='comment',
            index=models.Index(fields=['parent', 'created_at', '-id'], name='comments_top_created_asc'),
        ),
        migrations.RemoveIndex(
            model_name='comment',
            name='comments_co_parent__2efacb_idx',
        ),
    ]
