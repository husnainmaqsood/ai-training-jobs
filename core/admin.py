from django.contrib import admin

from .admin_mixins import AuditedAdminMixin
from .models import AuditEvent, ContactMessage, NewsletterSubscription, SystemErrorLog


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "category", "action", "actor", "target_repr")
    list_filter = ("category", "action")
    search_fields = ("action", "target_repr", "target_id")
    readonly_fields = [f.name for f in AuditEvent._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False  # append-only


@admin.register(SystemErrorLog)
class SystemErrorLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "exception_type", "path", "method")
    list_filter = ("exception_type",)
    readonly_fields = [f.name for f in SystemErrorLog._meta.fields]

    def has_add_permission(self, request):
        return False


@admin.register(NewsletterSubscription)
class NewsletterAdmin(AuditedAdminMixin, admin.ModelAdmin):
    list_display = ("email", "confirmed_at", "unsubscribed_at", "job_alerts", "alert_country", "created_at")
    list_filter = ("job_alerts",)
    search_fields = ("email",)
    readonly_fields = ("token", "consent_text", "consented_at", "confirmed_at")


@admin.register(ContactMessage)
class ContactMessageAdmin(admin.ModelAdmin):
    list_display = ("created_at", "subject", "email", "handled")
    list_filter = ("handled",)
    list_editable = ("handled",)
