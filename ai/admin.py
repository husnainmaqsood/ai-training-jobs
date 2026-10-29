from django.contrib import admin

from core.admin_mixins import AuditedAdminMixin

from .models import AIUsageRecord, ModelPrice


@admin.register(ModelPrice)
class ModelPriceAdmin(AuditedAdminMixin, admin.ModelAdmin):
    list_display = ("model", "input_per_million", "output_per_million", "cached_input_per_million", "updated_at")


@admin.register(AIUsageRecord)
class AIUsageRecordAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "user",
        "purpose",
        "model",
        "status",
        "input_tokens",
        "output_tokens",
        "estimated_cost_usd",
        "retry_count",
        "duration_ms",
    )
    list_filter = ("status", "purpose", "model", "provider")
    search_fields = ("user__email",)
    date_hierarchy = "created_at"
    readonly_fields = [f.name for f in AIUsageRecord._meta.fields]

    def has_add_permission(self, request):
        return False
