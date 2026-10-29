import secrets

from django.conf import settings
from django.db import models


class AuditEvent(models.Model):
    """Append-only record of sensitive administrative, billing and security actions."""

    class Category(models.TextChoices):
        ADMIN = "admin", "Admin"
        BILLING = "billing", "Billing"
        SECURITY = "security", "Security"
        DATA = "data", "Data / privacy"

    category = models.CharField(max_length=20, choices=Category.choices, default=Category.ADMIN, db_index=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    action = models.CharField(max_length=100, db_index=True)
    target_type = models.CharField(max_length=100, blank=True)
    target_id = models.CharField(max_length=64, blank=True)
    target_repr = models.CharField(max_length=255, blank=True)
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.category}:{self.action} {self.target_repr}"


class SystemErrorLog(models.Model):
    """Unhandled exceptions captured by middleware. Request bodies are never stored."""

    path = models.CharField(max_length=500)
    method = models.CharField(max_length=10)
    exception_type = models.CharField(max_length=200)
    message = models.TextField(blank=True)
    traceback = models.TextField(blank=True)
    user_id = models.BigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.exception_type} at {self.path}"


def _token():
    return secrets.token_urlsafe(32)


class NewsletterSubscription(models.Model):
    email = models.EmailField(unique=True)
    consent_text = models.CharField(max_length=500)
    consented_at = models.DateTimeField()
    confirmed_at = models.DateTimeField(null=True, blank=True)
    unsubscribed_at = models.DateTimeField(null=True, blank=True)
    token = models.CharField(max_length=64, unique=True, default=_token)
    job_alerts = models.BooleanField(default=False)
    alert_country = models.CharField(max_length=2, blank=True)
    alert_category = models.ForeignKey("jobs.Category", null=True, blank=True, on_delete=models.SET_NULL)
    last_alert_sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def is_active(self):
        return self.confirmed_at is not None and self.unsubscribed_at is None

    def __str__(self):
        return self.email


class ContactMessage(models.Model):
    name = models.CharField(max_length=120)
    email = models.EmailField()
    subject = models.CharField(max_length=200)
    message = models.TextField(max_length=5000)
    handled = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.subject} ({self.email})"
