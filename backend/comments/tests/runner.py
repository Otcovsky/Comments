from django.test.runner import DiscoverRunner

from comments import shared
from config.celery import app as celery_app


class Runner(DiscoverRunner):
    """Empties Redis before the tests: unlike the test database, it outlives a run, and the
    limits per IP, viewports and captchas of the last run would leak into this one.

    The tests have no broker: a queued task runs at once, in the thread that queued it."""

    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        shared.client().flushdb()
        celery_app.conf.task_always_eager = True
