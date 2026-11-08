# Tasks declared with shared_task bind to this app wherever Django is set up.
from .celery import app as celery_app

__all__ = ["celery_app"]
