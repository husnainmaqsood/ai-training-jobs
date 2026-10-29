import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVector, SearchVectorField
from django.db import models
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

# Query parameters that only track where a click came from; ignored when deciding if two links are the same job.
TRACKING_PARAMS = re.compile(r"^(utm_.*|ref|referrer|referral|source|src|gh_src|lever-source.*|fbclid|gclid|mc_.*|_hs.*)$", re.I)


def canonical_url(url):
    """Comparable form of a job link: host lower-cased without www, no scheme/fragment/trailing slash, tracking params removed."""
    parts = urlsplit((url or "").strip())
    if not parts.netloc:
        return ""
    host = parts.netloc.lower().removeprefix("www.")
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not TRACKING_PARAMS.match(k)))
    return f"{host}{parts.path.rstrip('/')}" + (f"?{query}" if query else "")


class Platform(models.Model):
    """A company or AI-training/data-work provider that publishes opportunities."""

    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(max_length=140, unique=True)
    website = models.URLField()
    careers_url = models.URLField(blank=True)
    description = models.TextField(blank=True, help_text="Admin-written, factual description.")
    work_types = models.CharField(max_length=255, blank=True, help_text="e.g. 'RLHF, coding evaluation, annotation'")
    is_active = models.BooleanField(default=True)
    last_reviewed_at = models.DateTimeField(null=True, blank=True)
    # Short hash of the current logo (see PlatformLogo); empty when there is none. Also busts browser caches.
    logo_version = models.CharField(max_length=16, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("jobs:platform_detail", args=[self.slug])

    @property
    def logo_url(self):
        return logo_url(self.slug, self.logo_version)


def logo_url(slug, version):
    return f"{reverse('jobs:platform_logo', args=[slug])}?v={version}" if version else ""


class PlatformLogo(models.Model):
    """A platform's logo as a normalised PNG (max 256 px). Kept apart from Platform so job queries never load the bytes."""

    platform = models.OneToOneField(Platform, on_delete=models.CASCADE, related_name="logo")
    image = models.BinaryField()
    source = models.CharField(max_length=500, blank=True, help_text="Where the logo came from (upload or URL).")
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Logo of {self.platform}"


class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=120, unique=True)
    description = models.TextField(blank=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name_plural = "categories"

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("jobs:category", args=[self.slug])


class Skill(models.Model):
    name = models.CharField(max_length=80, unique=True)
    slug = models.SlugField(max_length=100, unique=True)

    class Meta:
        ordering = ["name"]

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)[:100]
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("jobs:skill", args=[self.slug])


class JobSource(models.Model):
    """Where listings come from. Automated kinds require an explicit permission attestation."""

    class Kind(models.TextChoices):
        MANUAL = "manual", "Admin-managed manual listings"
        CSV = "csv", "CSV import"
        JSON_FEED = "json_feed", "Authorized JSON feed"
        GREENHOUSE = "greenhouse", "Greenhouse public job board API"
        LEVER = "lever", "Lever public postings API"

    AUTOMATED_KINDS = {Kind.JSON_FEED, Kind.GREENHOUSE, Kind.LEVER}

    name = models.CharField(max_length=150)
    platform = models.ForeignKey(Platform, on_delete=models.CASCADE, related_name="sources")
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.MANUAL)
    config = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            'Greenhouse: {"board_token": "acme"}. Lever: {"site": "acme"}. '
            'JSON feed: {"url": "https://...", "headers": {}}. Optional: {"default_category": "slug", '
            '"publish_immediately": false}'
        ),
    )
    permission_confirmed = models.BooleanField(
        default=False,
        help_text="I confirm this source's terms permit automated retrieval of these listings.",
    )
    terms_notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    refresh_interval_hours = models.PositiveIntegerField(default=12)
    expire_after_missed_runs = models.PositiveSmallIntegerField(
        default=2, help_text="Expire a listing after it is missing from this many consecutive successful refreshes."
    )
    last_run_at = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["platform__name", "name"]

    def __str__(self):
        return f"{self.name} ({self.get_kind_display()})"

    @property
    def is_automated(self):
        return self.kind in self.AUTOMATED_KINDS

    def is_due(self, now=None):
        now = now or timezone.now()
        if not self.last_run_at:
            return True
        return (now - self.last_run_at).total_seconds() >= self.refresh_interval_hours * 3600


class JobQuerySet(models.QuerySet):
    def public(self):
        """Listings that may be shown to visitors: published, not expired, not flagged broken."""
        now = timezone.now()
        qs = self.filter(status=Job.Status.PUBLISHED, link_broken=False).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
        if not getattr(settings, "SHOW_DEMO_JOBS", False):
            qs = qs.filter(is_demo=False)
        return qs

    def eligible_for_country(self, code):
        code = (code or "").upper()
        if not code:
            return self
        return self.filter(
            (Q(eligibility_scope=Job.Eligibility.WORLDWIDE) & ~Q(excluded_countries__contains=[code]))
            | (Q(eligibility_scope=Job.Eligibility.RESTRICTED) & Q(eligible_countries__contains=[code]))
        )


class Job(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"
        EXPIRED = "expired", "Expired"
        CLOSED = "closed", "Closed"
        REJECTED = "rejected", "Rejected"
        ARCHIVED = "archived", "Archived"

    class EmploymentType(models.TextChoices):
        CONTRACT = "contract", "Contract"
        FREELANCE = "freelance", "Freelance"
        HOURLY = "hourly", "Hourly / task-based"
        PART_TIME = "part_time", "Part-time"
        FULL_TIME = "full_time", "Full-time"
        INTERNSHIP = "internship", "Internship"

    class WorkArrangement(models.TextChoices):
        REMOTE = "remote", "Remote"
        HYBRID = "hybrid", "Hybrid"
        ONSITE = "onsite", "On-site"

    class Eligibility(models.TextChoices):
        WORLDWIDE = "worldwide", "Worldwide (verified)"
        RESTRICTED = "restricted", "Specific countries only"
        UNKNOWN = "unknown", "Not verified"

    class ExperienceLevel(models.TextChoices):
        ANY = "any", "Any level"
        ENTRY = "entry", "Entry level"
        MID = "mid", "Mid level"
        SENIOR = "senior", "Senior"
        EXPERT = "expert", "Expert / domain specialist"

    class CompPeriod(models.TextChoices):
        HOUR = "hour", "per hour"
        TASK = "task", "per task"
        WEEK = "week", "per week"
        MONTH = "month", "per month"
        YEAR = "year", "per year"
        PROJECT = "project", "per project"

    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, blank=True)
    platform = models.ForeignKey(Platform, on_delete=models.PROTECT, related_name="jobs")
    company_name = models.CharField(max_length=150, blank=True, help_text="If different from the platform.")
    source = models.ForeignKey(JobSource, null=True, blank=True, on_delete=models.SET_NULL, related_name="jobs")
    external_id = models.CharField(max_length=200, blank=True)
    fingerprint = models.CharField(max_length=64, db_index=True, editable=False)

    description = models.TextField(blank=True)
    preferred_qualifications = models.TextField(blank=True)
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.SET_NULL, related_name="jobs")
    skills = models.ManyToManyField(Skill, blank=True, related_name="jobs")
    tags = ArrayField(models.CharField(max_length=40), default=list, blank=True)

    employment_type = models.CharField(max_length=20, choices=EmploymentType.choices, default=EmploymentType.CONTRACT)
    work_arrangement = models.CharField(max_length=20, choices=WorkArrangement.choices, default=WorkArrangement.REMOTE)
    eligibility_scope = models.CharField(max_length=20, choices=Eligibility.choices, default=Eligibility.UNKNOWN)
    eligible_countries = ArrayField(models.CharField(max_length=2), default=list, blank=True)
    excluded_countries = ArrayField(models.CharField(max_length=2), default=list, blank=True)
    experience_level = models.CharField(max_length=20, choices=ExperienceLevel.choices, default=ExperienceLevel.ANY)

    compensation_min = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    compensation_max = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    compensation_currency = models.CharField(max_length=3, blank=True)
    compensation_period = models.CharField(max_length=10, choices=CompPeriod.choices, blank=True)
    compensation_verified = models.BooleanField(default=False, help_text="Only verified compensation is displayed publicly.")
    rate_text = models.CharField(
        max_length=120, blank=True, help_text="Rate as written by the admin (e.g. '$40–60/hr'). Shown when no structured rate is set."
    )
    # Normalized hourly USD estimate used only for filtering. Null when unknown/unverified.
    compensation_hourly_usd = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, editable=False)

    application_url = models.URLField(max_length=1000)
    canonical_url = models.CharField(max_length=1000, blank=True, editable=False, help_text="Normalised job link used to match re-imports.")
    referral_url = models.URLField(max_length=1000, blank=True, help_text="Only an approved referral URL from an active referral program.")
    source_url = models.URLField(max_length=1000, blank=True)

    published_at = models.DateTimeField(null=True, blank=True)
    last_verified_at = models.DateTimeField(null=True, blank=True)
    application_deadline = models.DateField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT, db_index=True)

    is_demo = models.BooleanField(default=False, help_text="Development demo data. Never shown in production.")
    link_broken = models.BooleanField(default=False)
    link_checked_at = models.DateTimeField(null=True, blank=True)
    needs_review = models.BooleanField(default=False)
    review_note = models.CharField(max_length=500, blank=True)
    missed_refreshes = models.PositiveSmallIntegerField(default=0)

    search_vector = SearchVectorField(null=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = JobQuerySet.as_manager()

    class Meta:
        ordering = ["-published_at", "-created_at"]
        indexes = [
            GinIndex(fields=["search_vector"]),
            GinIndex(fields=["eligible_countries"]),
            GinIndex(fields=["excluded_countries"]),
            models.Index(fields=["status", "-published_at"]),
            models.Index(fields=["expires_at"]),
            models.Index(fields=["platform", "canonical_url"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["source", "external_id"],
                condition=~Q(external_id=""),
                name="unique_external_id_per_source",
            ),
            models.UniqueConstraint(
                fields=["platform", "external_id"],
                condition=Q(source__isnull=True) & ~Q(external_id=""),
                name="unique_external_id_per_platform_for_uploads",
            ),
            models.CheckConstraint(
                condition=Q(compensation_min__isnull=True) | Q(compensation_max__isnull=True) | Q(compensation_min__lte=models.F("compensation_max")),
                name="compensation_min_lte_max",
            ),
        ]

    def __str__(self):
        return f"{self.title} — {self.platform}"

    # --- Derived values ---------------------------------------------------
    @staticmethod
    def compute_fingerprint(platform_id, title, application_url):
        norm_title = re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()
        norm_url = re.sub(r"[?#].*$", "", (application_url or "").strip().lower()).rstrip("/")
        return hashlib.sha256(f"{platform_id}|{norm_title}|{norm_url}".encode()).hexdigest()

    HOURS_PER_PERIOD = {"hour": 1, "week": 40, "month": 173, "year": 2080}

    def _hourly_usd(self):
        if not (self.compensation_verified and self.compensation_currency == "USD"):
            return None
        hours = self.HOURS_PER_PERIOD.get(self.compensation_period)
        amount = self.compensation_max or self.compensation_min
        if not hours or amount is None:
            return None
        return round(amount / hours, 2)

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title)[:200] or "job"
        self.fingerprint = self.compute_fingerprint(self.platform_id, self.title, self.application_url)
        self.canonical_url = canonical_url(self.application_url)
        self.eligible_countries = sorted({c.upper() for c in self.eligible_countries if c})
        self.excluded_countries = sorted({c.upper() for c in self.excluded_countries if c})
        if self.status == self.Status.PUBLISHED and not self.published_at:
            self.published_at = timezone.now()
        self.compensation_hourly_usd = self._hourly_usd()
        super().save(*args, **kwargs)
        self.refresh_search_vector()

    def refresh_search_vector(self):
        skills = " ".join(self.skills.values_list("name", flat=True)) if self.pk else ""
        Job.objects.filter(pk=self.pk).update(
            search_vector=SearchVector(models.Value(self.title), weight="A", config="english")
            + SearchVector(models.Value(skills + " " + " ".join(self.tags)), weight="B", config="english")
            + SearchVector(models.Value(self.platform.name + " " + self.company_name), weight="B", config="english")
            + SearchVector(models.Value(self.description + " " + self.preferred_qualifications), weight="C", config="english")
        )

    def get_absolute_url(self):
        return reverse("jobs:detail", args=[self.pk, self.slug])

    def get_apply_url(self):
        """Outbound redirect to the job's own link on the hiring platform (or its approved referral link); logs the click."""
        return reverse("referrals:go_job", args=[self.pk])

    @property
    def display_company(self):
        return self.company_name or self.platform.name

    @property
    def is_expired(self):
        if self.status in {self.Status.EXPIRED, self.Status.CLOSED}:
            return True
        return bool(self.expires_at and self.expires_at <= timezone.now())

    @property
    def is_public(self):
        return self.status == self.Status.PUBLISHED and not self.is_expired and not self.link_broken

    @property
    def has_referral(self):
        return bool(self.referral_url)

    @property
    def is_location_restricted(self):
        return self.eligibility_scope == self.Eligibility.RESTRICTED

    @property
    def show_compensation(self):
        return self.compensation_verified and (self.compensation_min is not None or self.compensation_max is not None)

    @property
    def eligibility_label(self):
        from core.countries import country_name

        if self.work_arrangement != self.WorkArrangement.REMOTE:
            base = self.get_work_arrangement_display()
        else:
            base = "Remote"
        if self.eligibility_scope == self.Eligibility.WORLDWIDE:
            if self.excluded_countries:
                return f"{base} · Worldwide (excl. {len(self.excluded_countries)} countries)"
            return f"{base} · Worldwide"
        if self.eligibility_scope == self.Eligibility.RESTRICTED:
            names = [country_name(c) for c in self.eligible_countries]
            if len(names) == 1:
                return f"{base} · {names[0]} only"
            if len(names) <= 3:
                return f"{base} · {', '.join(names)}"
            return f"{base} · {len(names)} eligible countries"
        return f"{base} · Location eligibility not verified"


class ImportRun(models.Model):
    """One feed refresh or one spreadsheet upload. Spreadsheet uploads have no `source` and keep their parsed rows."""

    class Status(models.TextChoices):
        PENDING = "pending", "Awaiting confirmation"
        RUNNING = "running", "Running"
        SUCCESS = "success", "Success"
        PARTIAL = "partial", "Partial (some rows rejected)"
        FAILED = "failed", "Failed — nothing changed"
        CANCELLED = "cancelled", "Cancelled"

    class Mode(models.TextChoices):
        UPSERT = "upsert", "Add & update"
        SNAPSHOT = "snapshot", "Full snapshot (close missing jobs)"

    source = models.ForeignKey(JobSource, null=True, blank=True, on_delete=models.CASCADE, related_name="runs")
    filename = models.CharField(max_length=200, blank=True)
    mode = models.CharField(max_length=10, choices=Mode.choices, default=Mode.UPSERT)
    rows = models.JSONField(default=list, blank=True, editable=False, help_text="Parsed spreadsheet rows, kept for preview and troubleshooting.")
    triggered_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RUNNING)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_count = models.PositiveIntegerField("added", default=0)
    updated_count = models.PositiveIntegerField("updated", default=0)
    duplicate_count = models.PositiveIntegerField("skipped", default=0)
    expired_count = models.PositiveIntegerField("closed / expired", default=0)
    failed_count = models.PositiveIntegerField("rejected", default=0)
    errors = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        label = self.source or f"Upload {self.filename}".strip()
        return f"{label} @ {self.started_at:%Y-%m-%d %H:%M} [{self.status}]"


class JobReport(models.Model):
    class Reason(models.TextChoices):
        SCAM = "scam", "Suspicious or scam"
        EXPIRED = "expired", "No longer available"
        BROKEN = "broken", "Broken link"
        INACCURATE = "inaccurate", "Inaccurate details"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        RESOLVED = "resolved", "Resolved"
        DISMISSED = "dismissed", "Dismissed"

    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="reports")
    reporter = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    reason = models.CharField(max_length=20, choices=Reason.choices)
    details = models.TextField(max_length=2000, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_reason_display()} — {self.job}"


class SavedJob(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="saved_jobs")
    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="saves")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["user", "job"], name="unique_saved_job")]

    def __str__(self):
        return f"{self.user} saved {self.job_id}"
