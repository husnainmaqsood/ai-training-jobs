import os

from celery import Celery
from celery.schedules import crontab

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("aitrainjobs")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

app.conf.beat_schedule = {
    "refresh-due-job-sources": {"task": "jobs.tasks.refresh_due_sources", "schedule": crontab(minute=15)},
    "expire-jobs": {"task": "jobs.tasks.expire_jobs", "schedule": crontab(minute=0)},
    "check-job-links": {"task": "jobs.tasks.check_links", "schedule": crontab(hour=3, minute=30)},
    "reconcile-billing": {"task": "billing.tasks.reconcile", "schedule": crontab(hour=4, minute=0)},
    "requeue-stuck-generations": {"task": "cvs.tasks.recover_stuck_generations", "schedule": crontab(minute="*/10")},
    "purge-expired-data": {"task": "core.tasks.purge_expired_data", "schedule": crontab(hour=5, minute=0)},
    "send-job-alerts": {"task": "core.tasks.send_job_alerts", "schedule": crontab(hour=8, minute=0, day_of_week="mon")},
}
