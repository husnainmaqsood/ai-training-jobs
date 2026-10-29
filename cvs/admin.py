from django.contrib import admin

from .models import GeneratedCV, GenerationRequest, MasterCV


# CV contents are personal data: admins see metadata only, never CV text.
@admin.register(MasterCV)
class MasterCVAdmin(admin.ModelAdmin):
    list_display = ("public_id", "user", "file_type", "parse_status", "created_at")
    list_filter = ("file_type", "parse_status")
    search_fields = ("user__email",)
    fields = ("public_id", "user", "title", "file_type", "parse_status", "parse_error", "is_primary", "created_at", "updated_at")
    readonly_fields = fields

    def has_add_permission(self, request):
        return False


@admin.register(GenerationRequest)
class GenerationRequestAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "status", "plan_code", "quota_consumed", "quota_refunded", "attempts")
    list_filter = ("status", "plan_code", "quota_refunded")
    search_fields = ("user__email",)
    fields = (
        "public_id",
        "user",
        "job",
        "status",
        "progress_message",
        "error_message",
        "quota_consumed",
        "quota_refunded",
        "attempts",
        "plan_code",
        "started_at",
        "finished_at",
        "created_at",
    )
    readonly_fields = fields

    def has_add_permission(self, request):
        return False


@admin.register(GeneratedCV)
class GeneratedCVAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "version", "match_score", "is_user_edited", "section_regenerations_used")
    search_fields = ("user__email",)
    fields = ("public_id", "user", "request", "version", "match_score", "is_user_edited", "section_regenerations_used", "created_at")
    readonly_fields = fields

    def has_add_permission(self, request):
        return False
