import uuid

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.db import models
from django.urls import reverse


def private_storage():
    # MEDIA_ROOT is set to PRIVATE_MEDIA_ROOT and no URL route serves it: files are only reachable
    # through ownership-checked download views. (No explicit location so settings overrides apply.)
    return FileSystemStorage()


def cv_upload_path(instance, filename):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
    return f"cvs/{instance.user_id}/{uuid.uuid4().hex}.{ext}"


class MasterCV(models.Model):
    """The user's original CV. Never modified by tailoring; generated versions are separate rows."""

    class ParseStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        TEXT_ONLY = "text_only", "Text extracted"
        STRUCTURED = "structured", "Structured"
        FAILED = "failed", "Failed"

    class FileType(models.TextChoices):
        PDF = "pdf", "PDF"
        DOCX = "docx", "DOCX"
        TEXT = "text", "Pasted text"

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="master_cvs")
    title = models.CharField(max_length=150)
    file_type = models.CharField(max_length=10, choices=FileType.choices)
    original_file = models.FileField(upload_to=cv_upload_path, storage=private_storage, blank=True)
    original_filename = models.CharField(max_length=255, blank=True)
    file_sha256 = models.CharField(max_length=64, blank=True)
    raw_text = models.TextField()
    structured = models.JSONField(default=dict, blank=True)
    parse_status = models.CharField(max_length=20, choices=ParseStatus.choices, default=ParseStatus.PENDING)
    parse_error = models.CharField(max_length=500, blank=True)
    is_primary = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_primary", "-updated_at"]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("cvs:master_detail", args=[self.public_id])

    def delete(self, *args, **kwargs):
        if self.original_file:
            self.original_file.delete(save=False)
        return super().delete(*args, **kwargs)


class GenerationRequest(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        PROCESSING = "processing", "Processing"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="generation_requests")
    master_cv = models.ForeignKey(MasterCV, on_delete=models.CASCADE, related_name="generation_requests")
    job = models.ForeignKey("jobs.Job", null=True, blank=True, on_delete=models.SET_NULL)
    target_title = models.CharField(max_length=200)
    target_company = models.CharField(max_length=150, blank=True)
    job_description = models.TextField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED, db_index=True)
    progress_message = models.CharField(max_length=200, blank=True)
    error_message = models.CharField(max_length=500, blank=True, help_text="Safe to show to the user.")
    quota_consumed = models.BooleanField(default=False)
    quota_refunded = models.BooleanField(default=False)
    attempts = models.PositiveSmallIntegerField(default=0)
    plan_code = models.CharField(max_length=50, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.target_title} [{self.status}]"

    def get_absolute_url(self):
        return reverse("cvs:generation_detail", args=[self.public_id])

    @property
    def is_finished(self):
        return self.status in {self.Status.SUCCEEDED, self.Status.FAILED}


class GeneratedCV(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="generated_cvs")
    request = models.ForeignKey(GenerationRequest, on_delete=models.CASCADE, related_name="versions")
    version = models.PositiveSmallIntegerField(default=1)
    title = models.CharField(max_length=200)
    content = models.JSONField(help_text="Tailored CV in the structured CV schema.")
    fit_analysis = models.JSONField(default=dict)
    match_score = models.PositiveSmallIntegerField(null=True, blank=True)
    validation_report = models.JSONField(default=dict)
    is_user_edited = models.BooleanField(default=False)
    section_regenerations_used = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["request", "version"], name="unique_generated_version")]

    def __str__(self):
        return f"{self.title} v{self.version}"

    def get_absolute_url(self):
        return reverse("cvs:generated_detail", args=[self.public_id])
