from django.contrib import admin

from core.admin_mixins import AuditedAdminMixin
from core.countries import normalize_list

from .models import ClickEvent, ReferralConversion, ReferralPayout, ReferralProgram


@admin.register(ReferralProgram)
class ReferralProgramAdmin(AuditedAdminMixin, admin.ModelAdmin):
    list_display = ("platform", "name", "status", "commission_verified", "last_verified_at")
    list_filter = ("status", "commission_verified")
    search_fields = ("platform__name", "name")

    def save_model(self, request, obj, form, change):
        obj.eligible_regions = normalize_list(obj.eligible_regions)
        super().save_model(request, obj, form, change)


@admin.register(ClickEvent)
class ClickEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "link_type", "platform", "job", "page", "is_authenticated")
    list_filter = ("link_type", "platform", "page")
    readonly_fields = [f.name for f in ClickEvent._meta.fields]
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(ReferralConversion)
class ReferralConversionAdmin(AuditedAdminMixin, admin.ModelAdmin):
    list_display = ("reported_at", "program", "external_reference", "status", "amount", "currency", "payout")
    list_filter = ("status", "program")
    raw_id_fields = ("job",)


@admin.register(ReferralPayout)
class ReferralPayoutAdmin(AuditedAdminMixin, admin.ModelAdmin):
    list_display = ("paid_at", "program", "amount", "currency", "reference")
    list_filter = ("program",)
