from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = (
        "Prints the database server's global status counters as tab-separated lines. Goes "
        "through the app's own connection, so it works for a local container and a cloud database alike."
    )

    def handle(self, *args, **options):
        if connection.vendor != "mysql":
            return
        with connection.cursor() as cursor:
            cursor.execute("SHOW GLOBAL STATUS")
            for name, value in cursor.fetchall():
                self.stdout.write(f"{name}\t{value}")
