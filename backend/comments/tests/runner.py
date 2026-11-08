from django.test.runner import DiscoverRunner

from comments import shared


class Runner(DiscoverRunner):
    """Empties Redis before the tests: unlike the test database, it outlives a run, and the
    limits per IP, viewports and captchas of the last run would leak into this one."""

    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        shared.client().flushdb()
