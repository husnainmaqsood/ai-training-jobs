from django.core.management.base import BaseCommand, CommandError

from billing.providers import BillingNotConfigured
from billing.reconcile import reconcile_subscriptions


class Command(BaseCommand):
    help = "Re-fetch non-ended subscriptions from the payment provider and repair any drift."

    def handle(self, *args, **opts):
        try:
            result = reconcile_subscriptions()
        except BillingNotConfigured as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f"Checked {result['checked']}, changed {result['changed']}.")
