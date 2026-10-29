from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.db.models import Q


class ReferralProgram(models.Model):
    """A platform's referral/affiliate program. Only `ACTIVE` programs with an approved URL are used."""

    class Status(models.TextChoices):
        UNVERIFIED = "unverified", "Not yet verified"
        ACTIVE = "active", "Active (approved)"
        PAUSED = "paused", "Paused"
        ENDED = "ended", "Ended"
        NOT_AVAILABLE = "not_available", "No public program"

    platform = models.ForeignKey("jobs.Platform", on_delete=models.CASCADE, related_name="referral_programs")
    name = models.CharField(max_length=150, default="Referral program")
    program_url = models.URLField(blank=True, help_text="Official page describing the program.")
    approved_referral_url = models.URLField(max_length=1000, blank=True)
    tracking_template = models.CharField(
        max_length=1000,
        blank=True,
        help_text="Optional template applied to a job's application URL, e.g. '{url}?ref=ABC'. Use only if the program terms allow per-job links.",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.UNVERIFIED)
    terms = models.TextField(blank=True)
    restrictions = models.TextField(blank=True)
    eligible_regions = ArrayField(models.CharField(max_length=2), default=list, blank=True)
    attribution_method = models.CharField(max_length=200, blank=True, help_text="e.g. 'referral link cookie', 'referral code'")
    commission_rules = models.TextField(blank=True)
    commission_verified = models.BooleanField(default=False)
    last_verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["platform__name"]
        constraints = [models.UniqueConstraint(fields=["platform"], condition=Q(status="active"), name="one_active_program_per_platform")]

    def __str__(self):
        return f"{self.platform} — {self.name} [{self.get_status_display()}]"

    @property
    def is_usable(self):
        return self.status == self.Status.ACTIVE and bool(self.approved_referral_url or self.tracking_template)

    def build_url_for(self, application_url):
        if not self.is_usable:
            return ""
        if self.tracking_template and "{url}" in self.tracking_template:
            return self.tracking_template.replace("{url}", application_url)
        return self.approved_referral_url


class ClickEvent(models.Model):
    """First-party outbound click. Stores no IP, user agent or user id — only a rotating, salted visitor hash."""

    class LinkType(models.TextChoices):
        APPLICATION = "application", "Direct application link"
        REFERRAL = "referral", "Referral link"
        PLATFORM = "platform", "Platform website"

    job = models.ForeignKey("jobs.Job", null=True, blank=True, on_delete=models.SET_NULL, related_name="clicks")
    platform = models.ForeignKey("jobs.Platform", null=True, blank=True, on_delete=models.SET_NULL, related_name="clicks")
    program = models.ForeignKey(ReferralProgram, null=True, blank=True, on_delete=models.SET_NULL, related_name="clicks")
    source = models.ForeignKey("jobs.JobSource", null=True, blank=True, on_delete=models.SET_NULL)
    link_type = models.CharField(max_length=20, choices=LinkType.choices)
    destination = models.URLField(max_length=1000)
    visitor_hash = models.CharField(max_length=64, db_index=True)
    is_authenticated = models.BooleanField(default=False)
    page = models.CharField(max_length=40, blank=True, help_text="Where the click came from (job_detail, job_list…)")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.link_type} click → {self.destination[:60]}"


class ReferralPayout(models.Model):
    program = models.ForeignKey(ReferralProgram, on_delete=models.PROTECT, related_name="payouts")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default="USD")
    paid_at = models.DateField()
    reference = models.CharField(max_length=200, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-paid_at"]

    def __str__(self):
        return f"{self.program.platform} payout {self.amount} {self.currency} on {self.paid_at}"


class ReferralConversion(models.Model):
    """A conversion as reported by the partner. Never created from our own click data."""

    class Status(models.TextChoices):
        REPORTED = "reported", "Reported by partner"
        APPROVED = "approved", "Commission approved"
        REJECTED = "rejected", "Rejected / reversed"
        PAID = "paid", "Paid out"

    program = models.ForeignKey(ReferralProgram, on_delete=models.PROTECT, related_name="conversions")
    job = models.ForeignKey("jobs.Job", null=True, blank=True, on_delete=models.SET_NULL)
    external_reference = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.REPORTED, db_index=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, default="USD")
    reported_at = models.DateField()
    approved_at = models.DateField(null=True, blank=True)
    payout = models.ForeignKey(ReferralPayout, null=True, blank=True, on_delete=models.SET_NULL, related_name="conversions")
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-reported_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["program", "external_reference"],
                condition=~Q(external_reference=""),
                name="unique_conversion_reference",
            )
        ]

    def __str__(self):
        return f"{self.program.platform} conversion {self.external_reference or self.pk} [{self.status}]"
