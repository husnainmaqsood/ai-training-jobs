from django.contrib import admin

from .models import Application


@admin.register(Application)
class ApplicationAdmin(admin.ModelAdmin):
    list_display = ("user", "title", "company", "status", "updated_at")
    list_filter = ("status",)
    search_fields = ("user__email", "title", "company")
    fields = ("user", "job", "title", "company", "status", "applied_at", "created_at", "updated_at")
    readonly_fields = fields  # notes are private to the user
