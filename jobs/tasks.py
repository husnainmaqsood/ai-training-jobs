import logging

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(name="jobs.tasks.refresh_source")
def refresh_source(source_id):
    from .importers import run_source
    from .models import JobSource

    source = JobSource.objects.get(pk=source_id)
    run = run_source(source)
    return run.status


@shared_task(name="jobs.tasks.refresh_due_sources")
def refresh_due_sources():
    from .models import JobSource

    now = timezone.now()
    due = [s for s in JobSource.objects.filter(is_active=True, permission_confirmed=True, kind__in=list(JobSource.AUTOMATED_KINDS)) if s.is_due(now)]
    for s in due:
        refresh_source.delay(s.pk)
    return len(due)


@shared_task(name="jobs.tasks.expire_jobs")
def expire_jobs():
    from .importers import expire_jobs as _expire

    return _expire()


@shared_task(name="jobs.tasks.check_links")
def check_links(limit=300):
    from .importers import check_link
    from .models import Job

    jobs = Job.objects.filter(status=Job.Status.PUBLISHED).order_by("link_checked_at")[:limit]
    broken = sum(1 for j in jobs if check_link(j))
    return broken
