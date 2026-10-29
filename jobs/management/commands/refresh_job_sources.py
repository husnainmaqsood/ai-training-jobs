from django.core.management.base import BaseCommand

from jobs.importers import run_source
from jobs.models import JobSource


class Command(BaseCommand):
    help = "Refresh automated job sources (cron-friendly alternative to Celery beat)."

    def add_arguments(self, parser):
        parser.add_argument("--all", action="store_true", help="Ignore refresh intervals")
        parser.add_argument("--source", type=int, help="Only this source id")

    def handle(self, *args, **opts):
        qs = JobSource.objects.filter(is_active=True, kind__in=list(JobSource.AUTOMATED_KINDS))
        if opts.get("source"):
            qs = qs.filter(pk=opts["source"])
        for source in qs:
            if not (opts["all"] or opts.get("source") or source.is_due()):
                continue
            run = run_source(source)
            self.stdout.write(
                f"{source}: {run.status} +{run.created_count} ~{run.updated_count} dup={run.duplicate_count} "
                f"expired={run.expired_count} failed={run.failed_count}"
            )
