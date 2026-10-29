from django.conf import settings
from django.db import models


class ModelPrice(models.Model):
    """Admin-configurable model pricing (USD per 1M tokens), used for cost estimates."""

    model = models.CharField(max_length=100, unique=True)
    input_per_million = models.DecimalField(max_digits=10, decimal_places=4)
    output_per_million = models.DecimalField(max_digits=10, decimal_places=4)
    cached_input_per_million = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    notes = models.CharField(max_length=255, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.model}: in ${self.input_per_million}/M, out ${self.output_per_million}/M"


class AIUsageRecord(models.Model):
    class Status(models.TextChoices):
        SUCCESS = "success", "Success"
        INVALID_OUTPUT = "invalid_output", "Invalid output"
        ERROR = "error", "Provider error"
        RATE_LIMITED = "rate_limited", "Rate limited"
        TIMEOUT = "timeout", "Timeout"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="ai_usage")
    generation = models.ForeignKey("cvs.GenerationRequest", null=True, blank=True, on_delete=models.SET_NULL, related_name="ai_usage")
    purpose = models.CharField(max_length=40)
    provider = models.CharField(max_length=40)
    model = models.CharField(max_length=100)
    input_tokens = models.PositiveIntegerField(default=0)
    cached_input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    estimated_cost_usd = models.DecimalField(max_digits=10, decimal_places=6, default=0)
    cost_is_estimated = models.BooleanField(default=True, help_text="False if no price was configured for the model.")
    status = models.CharField(max_length=20, choices=Status.choices)
    retry_count = models.PositiveSmallIntegerField(default=0)
    duration_ms = models.PositiveIntegerField(default=0)
    error_type = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "created_at"])]

    def __str__(self):
        return f"{self.purpose} {self.model} {self.status} ${self.estimated_cost_usd}"
