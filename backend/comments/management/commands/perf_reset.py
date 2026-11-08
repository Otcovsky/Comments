from comments import shared
from config.celery import app as celery_app

from .seed_perf import Command as SeedCommand


class Command(SeedCommand):
    help = (
        "Brings the app to the same state before every load test run: the seed_perf data set "
        "and nothing kept from the previous run. Takes the same arguments as seed_perf."
    )

    def handle(self, *args, **options):
        super().handle(*args, **options)
        # Everything Redis keeps (captchas, limits, viewports, the cached comments) and the
        # tasks still queued in RabbitMQ go too, or the run starts warm. perf/remote/app.sh then
        # restarts every service, and anything added later that outlives a restart (a search
        # index) must be emptied here as well.
        shared.client().flushdb()
        celery_app.control.purge()
