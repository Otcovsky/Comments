from django.db import migrations, models


class Migration(migrations.Migration):
    """The original name of an attachment, for the interface. Comments saved before it keep an
    empty name. MySQL 8 adds the column instantly, without rebuilding the table."""

    dependencies = [
        ('comments', '0006_date_indexes_with_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='comment',
            name='attachment_name',
            field=models.CharField(blank=True, max_length=255),
        ),
    ]
