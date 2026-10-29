from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import User
from core.audit import record


class Command(BaseCommand):
    help = "Make <email> the site's only admin. The current admin (if any) becomes a regular user."

    def add_arguments(self, parser):
        parser.add_argument("email")

    @transaction.atomic
    def handle(self, email, **options):
        try:
            user = User.objects.select_for_update().get(email__iexact=email.strip())
        except User.DoesNotExist:
            raise CommandError(f"No account with email {email}. Register it on the site first.") from None
        previous = User.objects.select_for_update().filter(is_staff=True).exclude(pk=user.pk).first()
        if previous:
            User.objects.filter(pk=previous.pk).update(is_staff=False, is_superuser=False)
        User.objects.filter(pk=user.pk).update(is_staff=True, is_superuser=True, is_active=True)
        record("admin_changed", target=user, previous=previous.email if previous else "")
        self.stdout.write(self.style.SUCCESS(f"{user.email} is now the only admin." + (f" {previous.email} is a regular user." if previous else "")))
