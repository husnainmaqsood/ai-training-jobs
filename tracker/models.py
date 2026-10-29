from django.conf import settings
from django.db import models
from django.db.models import Q


class Application(models.Model):
    """A user-maintained record of an application. We never submit applications on the user's behalf."""

    class Status(models.TextChoices):
        SAVED = "saved", "Job saved"
        CV_TAILORED = "cv_tailored", "CV tailored"
        PREPARED = "prepared", "Application prepared"
        APPLIED = "applied", "Applied"
        ASSESSMENT = "assessment", "Assessment pending"
        INTERVIEW = "interview", "Interview"
        REJECTED = "rejected", "Rejected"
        OFFER = "offer", "Offer"
        WITHDRAWN = "withdrawn", "Withdrawn"

    ACTIVE_STATUSES = {Status.SAVED, Status.CV_TAILORED, Status.PREPARED, Status.APPLIED, Status.ASSESSMENT, Status.INTERVIEW}

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="applications")
    job = models.ForeignKey("jobs.Job", null=True, blank=True, on_delete=models.SET_NULL, related_name="applications")
    title = models.CharField(max_length=200)
    company = models.CharField(max_length=150, blank=True)
    external_url = models.URLField(max_length=1000, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SAVED, db_index=True)
    generated_cv = models.ForeignKey("cvs.GeneratedCV", null=True, blank=True, on_delete=models.SET_NULL, related_name="applications")
    notes = models.TextField(blank=True, max_length=10000)
    applied_at = models.DateField(null=True, blank=True, help_text="Date you submitted the application yourself.")
    next_step_at = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        constraints = [models.UniqueConstraint(fields=["user", "job"], condition=Q(job__isnull=False), name="one_application_per_job")]

    def __str__(self):
        return f"{self.title} @ {self.company} [{self.status}]"


class ApplicationEvent(models.Model):
    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name="events")
    from_status = models.CharField(max_length=20, blank=True)
    to_status = models.CharField(max_length=20)
    note = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.from_status or '—'} → {self.to_status}"
