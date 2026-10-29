from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from core.admin_mixins import AuditedAdminMixin

from .models import Profile, User


class ProfileInline(admin.StackedInline):
    model = Profile
    can_delete = False
    extra = 0


@admin.register(User)
class UserAdmin(AuditedAdminMixin, BaseUserAdmin):
    ordering = ("-date_joined",)
    list_display = ("email", "email_verified", "is_staff", "is_active", "date_joined", "last_login")
    list_filter = ("email_verified", "is_staff", "is_active")
    search_fields = ("email", "profile__full_name")
    inlines = [ProfileInline]
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Status", {"fields": ("email_verified", "is_active", "is_staff", "is_superuser")}),
        ("Dates", {"fields": ("last_login", "date_joined")}),
    )
    # One admin only (enforced in the database); hand the role over with `manage.py set_admin <email>`.
    readonly_fields = ("is_staff", "is_superuser", "last_login", "date_joined")

    def has_delete_permission(self, request, obj=None):
        return super().has_delete_permission(request, obj) and not (obj and obj.is_staff)

    def get_readonly_fields(self, request, obj=None):
        # The admin can't lock themselves out.
        fields = super().get_readonly_fields(request, obj)
        return (*fields, "is_active") if obj and obj.is_staff else fields

    add_fieldsets = ((None, {"classes": ("wide",), "fields": ("email", "password1", "password2")}),)
