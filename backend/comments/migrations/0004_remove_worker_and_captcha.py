from django.db import migrations, models


class Migration(migrations.Migration):
    """Attachments are processed inside the request, and captchas live in process memory."""

    dependencies = [
        ('comments', '0003_comment_comments_co_attachm_e01fd7_idx'),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name='comment',
            name='comments_co_attachm_e01fd7_idx',
        ),
        migrations.RemoveField(
            model_name='comment',
            name='attachment_status',
        ),
        migrations.AlterField(
            model_name='comment',
            name='attachment',
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.DeleteModel(
            name='Captcha',
        ),
    ]
