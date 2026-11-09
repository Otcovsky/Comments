import time

from botocore.exceptions import BotoCoreError, ClientError
from django.core.management.base import BaseCommand, CommandError

from comments import storage


class Command(BaseCommand):
    help = (
        "Creates the bucket from S3_BUCKET unless it exists. For the local run "
        "(docker-compose.local.yml), whose storage starts empty; a cloud bucket is created in the cloud."
    )

    def add_arguments(self, parser):
        parser.add_argument("--wait", type=int, default=60, help="Seconds to wait for the storage to answer.")

    def handle(self, *args, wait, **options):
        deadline = time.monotonic() + wait
        while True:
            try:
                created = storage.create_bucket()
                break
            except (BotoCoreError, ClientError) as error:
                # The storage container is up before its S3 port answers.
                if time.monotonic() > deadline:
                    raise CommandError(f"The storage did not answer: {error}")
                time.sleep(2)
        self.stdout.write("Bucket created." if created else "Bucket exists.")
