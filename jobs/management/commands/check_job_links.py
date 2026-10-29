from django.core.management.base import BaseCommand

from jobs.tasks import check_links


class Command(BaseCommand):
    help = "Check application links of published jobs; 404/410 links are disabled and flagged for review."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=300)

    def handle(self, *args, **opts):
        self.stdout.write(f"Flagged {check_links(opts['limit'])} broken link(s).")
