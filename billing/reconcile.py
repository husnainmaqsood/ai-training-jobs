"""Compare local subscriptions with the provider and repair drift (e.g. missed webhooks)."""

import logging
import time

from django.db import transaction

from core.audit import record
from core.models import AuditEvent

from .models import Subscription
from .providers import BillingNotConfigured, BillingProviderError, get_provider
from .webhooks import sync_subscription

logger = logging.getLogger(__name__)


def reconcile_subscriptions(limit=500):
    provider = get_provider()
    if not provider.is_configured():
        raise BillingNotConfigured("Billing provider is not configured")
    changed = checked = 0
    subs = Subscription.objects.exclude(status__in=[Subscription.Status.CANCELED, Subscription.Status.INCOMPLETE_EXPIRED])[:limit]
    for sub in subs:
        checked += 1
        try:
            remote = provider.fetch_subscription(sub.provider_subscription_id)
        except BillingProviderError:
            logger.warning("Could not fetch subscription %s", sub.provider_subscription_id)
            continue
        before = (sub.status, sub.current_period_end, sub.cancel_at_period_end, sub.plan_id)
        with transaction.atomic():
            sync_subscription(remote, int(time.time()))
        sub.refresh_from_db()
        after = (sub.status, sub.current_period_end, sub.cancel_at_period_end, sub.plan_id)
        if before != after:
            changed += 1
            record(
                "reconciled_subscription",
                target=sub.user,
                category=AuditEvent.Category.BILLING,
                subscription=sub.provider_subscription_id,
                before=str(before),
                after=str(after),
            )
    return {"checked": checked, "changed": changed}
