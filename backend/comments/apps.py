from django.apps import AppConfig


class CommentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "comments"
    verbose_name = "Комментарии"

    def ready(self):
        from django.db.models.signals import post_delete, post_migrate, post_save

        from . import cache, queries
        from .models import Comment

        def saved(instance, created, **kwargs):
            if not created:
                return
            if instance.parent_id is None:
                queries.top_level_count.forget()
            else:
                cache.forget_replies(instance.parent_id)

        def deleted(instance, **kwargs):
            queries.top_level_count.forget()
            cache.forget_comment(instance.pk)
            if instance.parent_id is not None:
                cache.forget_replies(instance.parent_id)

        def flushed(**kwargs):
            queries.top_level_count.forget()
            cache.forget_all()

        post_save.connect(saved, sender=Comment, weak=False)
        post_delete.connect(deleted, sender=Comment, weak=False)
        # A flush replaces every row without signals, and the new rows may reuse old ids:
        # tests flush between cases.
        post_migrate.connect(flushed, weak=False)
