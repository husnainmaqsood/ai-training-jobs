"""Data-retention housekeeping. Safe to run daily (cron or Celery beat)."""

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from billing.models import WebhookEvent
from core.models import NewsletterSubscription, SystemErrorLog
from cvs.models import GenerationRequest
from referrals.models import ClickEvent


class Command(BaseCommand):
    help = "Delete data past its retention period (see docs/SECURITY.md)."

    def handle(self, *args, **opts):
        now = timezone.now()
        r = getattr(settings, "RETENTION_DAYS", {})
        results = {
            "click_events": ClickEvent.objects.filter(created_at__lt=now - timedelta(days=r.get("clicks", 400))).delete()[0],
            "error_logs": SystemErrorLog.objects.filter(created_at__lt=now - timedelta(days=r.get("errors", 90))).delete()[0],
            "webhook_payloads": WebhookEvent.objects.filter(
                received_at__lt=now - timedelta(days=r.get("webhooks", 400)), status__in=["processed", "ignored"]
            ).update(payload={}),
            "unconfirmed_newsletter": NewsletterSubscription.objects.filter(
                confirmed_at__isnull=True, created_at__lt=now - timedelta(days=r.get("unconfirmed_newsletter", 30))
            ).delete()[0],
            # Failed generations keep no output; drop their pasted job descriptions after a while.
            "failed_generation_inputs": GenerationRequest.objects.filter(
                status=GenerationRequest.Status.FAILED, created_at__lt=now - timedelta(days=r.get("failed_generations", 30))
            )
            .exclude(job_description="")
            .update(job_description=""),
        }
        for k, v in results.items():
            self.stdout.write(f"{k}: {v}")
