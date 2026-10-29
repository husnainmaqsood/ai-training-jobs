from core.audit import record


class AuditedAdminMixin:
    """Writes an AuditEvent for every admin create/update/delete and bulk action."""

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        record("admin_update" if change else "admin_create", actor=request.user, target=obj, changed=list(form.changed_data) if form else [])

    def delete_model(self, request, obj):
        record("admin_delete", actor=request.user, target=obj)
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        for obj in queryset:
            record("admin_delete", actor=request.user, target=obj, bulk=True)
        super().delete_queryset(request, queryset)

    def audit_action(self, request, queryset, action):
        for obj in queryset:
            record(action, actor=request.user, target=obj)
