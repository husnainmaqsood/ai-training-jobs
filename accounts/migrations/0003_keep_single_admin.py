import os

from django.db import migrations
from django.db.models import F


def keep_single_admin(apps, schema_editor):
    """Before the single-admin constraint: keep ADMIN_EMAIL (if set) or the most recently active admin; demote the rest."""
    User = apps.get_model("accounts", "User")
    admins = User.objects.filter(is_staff=True) | User.objects.filter(is_superuser=True)
    wanted = os.environ.get("ADMIN_EMAIL", "").strip().lower()
    keep = (wanted and admins.filter(email__iexact=wanted).first()) or admins.order_by(F("last_login").desc(nulls_last=True), "date_joined").first()
    if keep is None:
        return
    admins.exclude(pk=keep.pk).update(is_staff=False, is_superuser=False)
    User.objects.filter(pk=keep.pk).update(is_staff=True, is_superuser=True)


class Migration(migrations.Migration):
    dependencies = [("accounts", "0002_profile_additional_info_profile_education_and_more")]

    operations = [migrations.RunPython(keep_single_admin, migrations.RunPython.noop)]
