from django.db import migrations, models


class Migration(migrations.Migration):
    """A copy of the author's pair in every comment: the list sorts and counts by name and
    e-mail with an index instead of joining every top-level comment with its author."""

    dependencies = [
        ('comments', '0004_remove_worker_and_captcha'),
    ]

    operations = [
        migrations.AddField(
            model_name='comment',
            name='username',
            field=models.CharField(default='', max_length=64),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='comment',
            name='email',
            field=models.EmailField(default='', max_length=254),
            preserve_default=False,
        ),
        migrations.RunSQL(
            'UPDATE comments_comment c JOIN comments_author a ON a.id = c.author_id '
            'SET c.username = a.username, c.email = a.email',
            migrations.RunSQL.noop,
        ),
        migrations.AddIndex(
            model_name='comment',
            index=models.Index(fields=['parent', 'username', '-id'], name='comments_top_username_asc'),
        ),
        migrations.AddIndex(
            model_name='comment',
            index=models.Index(fields=['parent', '-username', '-id'], name='comments_top_username_desc'),
        ),
        migrations.AddIndex(
            model_name='comment',
            index=models.Index(fields=['parent', 'email', '-id'], name='comments_top_email_asc'),
        ),
        migrations.AddIndex(
            model_name='comment',
            index=models.Index(fields=['parent', '-email', '-id'], name='comments_top_email_desc'),
        ),
    ]
