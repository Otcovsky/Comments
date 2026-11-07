from django.apps import AppConfig


class CommentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "comments"
    verbose_name = "Комментарии"

    def ready(self):
        from django.db.models.signals import post_delete, post_migrate, post_save

        from . import queries
        from .models import Comment

        def saved(instance, created, **kwargs):
            if created and instance.parent_id is None:
                queries.top_level_count.forget()

        post_save.connect(saved, sender=Comment, weak=False)
        post_delete.connect(queries.top_level_count.forget, sender=Comment, weak=False)
        # A flush replaces every row without signals: tests do it between cases.
        post_migrate.connect(queries.top_level_count.forget, weak=False)
