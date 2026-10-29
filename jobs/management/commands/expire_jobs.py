from django.core.management.base import BaseCommand

from jobs.importers import expire_jobs


class Command(BaseCommand):
    help = "Mark listings past their expiry date or application deadline as expired."

    def handle(self, *args, **opts):
        self.stdout.write(f"Expired {expire_jobs()} listing(s).")
