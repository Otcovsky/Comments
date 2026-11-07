from .seed_perf import Command as SeedCommand


class Command(SeedCommand):
    help = (
        "Brings the app to the same state before every load test run: the seed_perf data set "
        "and nothing kept from the previous run. Takes the same arguments as seed_perf."
    )

    def handle(self, *args, **options):
        # Now only the backend's memory keeps state between requests (captchas, limits,
        # viewports), and perf/remote/app.sh restarts the backend after this command. Anything
        # added later that outlives a restart (a cache, a task queue, a search index) must be
        # emptied here, or the first run after the change starts warm.
        super().handle(*args, **options)
