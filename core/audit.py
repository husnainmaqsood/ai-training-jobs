from core.models import AuditEvent


def record(action, *, actor=None, target=None, category=AuditEvent.Category.ADMIN, **details):
    kwargs = {}
    if target is not None:
        kwargs = {
            "target_type": f"{target._meta.app_label}.{target._meta.model_name}",
            "target_id": str(target.pk),
            "target_repr": str(target)[:255],
        }
    return AuditEvent.objects.create(
        category=category,
        actor=actor if (actor is not None and getattr(actor, "is_authenticated", False)) else None,
        action=action,
        details=details,
        **kwargs,
    )
