from django.core.management.base import BaseCommand, CommandError

from jobs.importers import run_source
from jobs.models import JobSource


class Command(BaseCommand):
    help = "Import listings from a CSV file into a job source."

    def add_arguments(self, parser):
        parser.add_argument("source_id", type=int)
        parser.add_argument("csv_path")

    def handle(self, source_id, csv_path, **opts):
        try:
            source = JobSource.objects.get(pk=source_id)
        except JobSource.DoesNotExist as exc:
            raise CommandError("No such source") from exc
        with open(csv_path, "rb") as fh:
            run = run_source(source, csv_file=fh)
        self.stdout.write(f"{run.status}: +{run.created_count} ~{run.updated_count} dup={run.duplicate_count} failed={run.failed_count}")
        for err in run.errors[:20]:
            self.stdout.write(f"  {err}")
