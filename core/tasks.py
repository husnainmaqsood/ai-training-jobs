from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.urls import reverse
from django.utils import timezone


@shared_task(name="core.tasks.send_job_alerts")
def send_job_alerts():
    """Weekly digest of new real listings for confirmed subscribers who opted in to job alerts."""
    from jobs.models import Job

    from .models import NewsletterSubscription

    since = timezone.now() - timedelta(days=7)
    sent = 0
    subs = NewsletterSubscription.objects.filter(job_alerts=True, confirmed_at__isnull=False, unsubscribed_at__isnull=True)
    for sub in subs:
        jobs = Job.objects.public().filter(published_at__gte=since, is_demo=False)
        if sub.alert_country:
            jobs = jobs.eligible_for_country(sub.alert_country)
        if sub.alert_category_id:
            jobs = jobs.filter(category_id=sub.alert_category_id)
        jobs = list(jobs.select_related("platform")[:15])
        if not jobs:
            continue
        lines = [f"- {j.title} ({j.display_company}) — {j.eligibility_label}\n  {settings.SITE_URL}{j.get_absolute_url()}" for j in jobs]
        unsub = settings.SITE_URL + reverse("core:newsletter_unsubscribe", args=[sub.token])
        body = "New remote AI training listings this week:\n\n" + "\n".join(lines) + f"\n\nUnsubscribe: {unsub}\n"
        send_mail(f"{len(jobs)} new AI training jobs this week", body, settings.DEFAULT_FROM_EMAIL, [sub.email])
        sub.last_alert_sent_at = timezone.now()
        sub.save(update_fields=["last_alert_sent_at"])
        sent += 1
    return sent


@shared_task(name="core.tasks.purge_expired_data")
def purge_expired_data():
    from django.core.management import call_command

    call_command("purge_expired_data")
