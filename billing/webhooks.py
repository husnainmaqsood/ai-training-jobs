"""
Webhook processing. Provider-verified events are the only thing that changes subscription status.

Guarantees:
* Signature is verified before anything is stored (see views.stripe_webhook).
* Each event id is processed at most once (unique WebhookEvent row + row lock); duplicates are acknowledged.
* Out-of-order delivery: a subscription is only updated from an event whose provider timestamp is not older
  than the last applied event for that subscription.
"""

import logging
from datetime import UTC, datetime

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from core.audit import record
from core.models import AuditEvent

from .models import BillingCustomer, Invoice, Plan, Subscription, WebhookEvent

logger = logging.getLogger(__name__)
User = get_user_model()


def _ts(value):
    if not value:
        return None
    return datetime.fromtimestamp(int(value), tz=UTC)


def _audit(action, user=None, **details):
    record(action, actor=None, target=user, category=AuditEvent.Category.BILLING, **details)


def process_event(provider_name, payload):
    """Store and process a verified webhook payload. Returns the WebhookEvent."""
    with transaction.atomic():
        event, created = WebhookEvent.objects.select_for_update().get_or_create(
            provider=provider_name,
            event_id=payload.id,
            defaults={
                "event_type": payload.type,
                "provider_created_at": _ts(payload.created),
                "payload": payload.raw,
            },
        )
        if not created and event.status in {WebhookEvent.Status.PROCESSED, WebhookEvent.Status.IGNORED}:
            logger.info("Duplicate webhook %s ignored", payload.id)
            return event
        event.attempts += 1
        try:
            with transaction.atomic():
                handled = dispatch(payload)
            event.status = WebhookEvent.Status.PROCESSED if handled else WebhookEvent.Status.IGNORED
            event.error = ""
        except Exception as exc:  # recorded and surfaced to provider for retry
            logger.exception("Webhook %s failed", payload.id)
            event.status = WebhookEvent.Status.FAILED
            event.error = f"{type(exc).__name__}: {exc}"[:2000]
        event.processed_at = timezone.now()
        event.save()
    return event


def dispatch(payload):
    obj = payload.data.get("object", {}) or {}
    t = payload.type
    if t in {"checkout.session.completed", "checkout.session.async_payment_succeeded"}:
        return handle_checkout_completed(obj, payload.created)
    if t in {
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
        "customer.subscription.paused",
        "customer.subscription.resumed",
    }:
        return sync_subscription(obj, payload.created)
    if t in {
        "invoice.paid",
        "invoice.payment_succeeded",
        "invoice.payment_failed",
        "invoice.finalized",
        "invoice.voided",
        "invoice.marked_uncollectible",
    }:
        return sync_invoice(obj, failed=(t == "invoice.payment_failed"))
    if t in {"charge.refunded", "refund.created", "refund.updated"}:
        return handle_refund(obj, t)
    return False


# --- Handlers -------------------------------------------------------------------


def _user_for(customer_id=None, metadata=None, client_reference_id=None):
    if customer_id:
        bc = BillingCustomer.objects.filter(provider_customer_id=customer_id).select_related("user").first()
        if bc:
            return bc.user
    user_id = (metadata or {}).get("user_id") or client_reference_id
    if user_id and str(user_id).isdigit():
        return User.objects.filter(pk=int(user_id)).first()
    return None


def handle_credit_purchase(session):
    """Grant credits for a paid one-time checkout. Idempotent on the checkout session id."""
    from . import credits

    if session.get("payment_status") != "paid":
        return False  # e.g. delayed payment methods; checkout.session.async_payment_succeeded follows
    meta = session.get("metadata") or {}
    user = _user_for(None, meta, session.get("client_reference_id"))
    if user is None:
        raise ValueError("credit checkout without resolvable user")
    pack = credits.get_pack(meta.get("pack", ""))
    # The amount comes from our own pack definition, checked against what was actually paid.
    if pack is None or int(session.get("amount_total") or 0) < pack.price_cents:
        raise ValueError(f"credit checkout {session.get('id')} does not match a configured pack")
    txn = credits.grant(
        user,
        pack.credits,
        credits.CreditTransaction.Kind.PURCHASE,
        reference=session.get("id", ""),
        note=f"pack={pack.code}",
        amount_paid_cents=int(session.get("amount_total") or 0),
        currency=session.get("currency") or "",
    )
    _audit("credits_purchased", user, session=session.get("id"), pack=pack.code, granted=bool(txn))
    return True


def handle_checkout_completed(session, created):
    if session.get("mode") == "payment" and (session.get("metadata") or {}).get("kind") == "cv_credits":
        return handle_credit_purchase(session)
    if session.get("mode") != "subscription":
        return False
    user = _user_for(None, session.get("metadata"), session.get("client_reference_id"))
    customer_id = session.get("customer")
    if user is None:
        raise ValueError("checkout.session.completed without resolvable user")
    if customer_id:
        BillingCustomer.objects.get_or_create(user=user, defaults={"provider_customer_id": customer_id})
    _audit("checkout_completed", user, session=session.get("id"), subscription=session.get("subscription"))
    # Access is granted by the subscription object, not by the checkout session. If the provider API is
    # configured we fetch it now so the user doesn't wait for customer.subscription.created.
    sub_id = session.get("subscription")
    if sub_id:
        from .providers import BillingNotConfigured, get_provider

        try:
            provider = get_provider()
            if provider.is_configured():
                sync_subscription(provider.fetch_subscription(sub_id), created)
        except BillingNotConfigured:
            pass
    return True


def _period(obj):
    start = obj.get("current_period_start")
    end = obj.get("current_period_end")
    items = (obj.get("items") or {}).get("data") or []
    if (not start or not end) and items:  # newer Stripe API versions store periods on items
        start = start or items[0].get("current_period_start")
        end = end or items[0].get("current_period_end")
    return _ts(start), _ts(end)


def _price_id(obj):
    items = (obj.get("items") or {}).get("data") or []
    if items:
        price = items[0].get("price") or {}
        return price.get("id") if isinstance(price, dict) else price
    plan = obj.get("plan") or {}
    return plan.get("id") if isinstance(plan, dict) else None


def sync_subscription(obj, event_created):
    sub_id = obj.get("id")
    if not sub_id:
        return False
    event_time = _ts(event_created) or timezone.now()
    user = _user_for(obj.get("customer"), obj.get("metadata"))
    existing = Subscription.objects.select_for_update().filter(provider_subscription_id=sub_id).first()
    if user is None and existing:
        user = existing.user
    if user is None:
        raise ValueError(f"Subscription {sub_id} has no resolvable user")

    if existing and existing.last_event_at and event_time < existing.last_event_at:
        logger.info("Out-of-order subscription event for %s ignored", sub_id)
        _audit("subscription_event_out_of_order", user, subscription=sub_id)
        return False

    price_id = _price_id(obj)
    plan = Plan.objects.filter(provider_price_id=price_id).first() if price_id else None
    if plan is None:
        plan = existing.plan if existing else None
    if plan is None:
        raise ValueError(f"No plan configured for price {price_id}")

    status = obj.get("status") or Subscription.Status.INCOMPLETE
    start, end = _period(obj)
    sub = existing or Subscription(user=user, provider_subscription_id=sub_id, provider="stripe")
    previous_status = sub.status if existing else None
    sub.plan = plan
    sub.status = status
    sub.current_period_start = start or sub.current_period_start
    sub.current_period_end = end or sub.current_period_end
    sub.cancel_at_period_end = bool(obj.get("cancel_at_period_end"))
    sub.canceled_at = _ts(obj.get("canceled_at"))
    sub.ended_at = _ts(obj.get("ended_at"))
    if status == Subscription.Status.PAST_DUE and not sub.past_due_since:
        sub.past_due_since = event_time
    elif status != Subscription.Status.PAST_DUE:
        sub.past_due_since = None
    sub.last_event_at = event_time
    sub.save()
    if previous_status != status:
        _audit("subscription_status_changed", user, subscription=sub_id, old=previous_status, new=status, plan=plan.code)
    return True


def sync_invoice(obj, failed=False):
    inv_id = obj.get("id")
    if not inv_id:
        return False
    user = _user_for(obj.get("customer"), obj.get("metadata"))
    if user is None:
        raise ValueError(f"Invoice {inv_id} has no resolvable user")
    sub_ref = obj.get("subscription")
    if not sub_ref:  # newer API versions nest this under parent.subscription_details
        sub_ref = ((obj.get("parent") or {}).get("subscription_details") or {}).get("subscription")
    subscription = Subscription.objects.filter(provider_subscription_id=sub_ref).first() if sub_ref else None
    lines = (obj.get("lines") or {}).get("data") or []
    period = (lines[0].get("period") if lines else None) or {}
    Invoice.objects.update_or_create(
        provider_invoice_id=inv_id,
        defaults={
            "user": user,
            "subscription": subscription,
            "number": obj.get("number") or "",
            "status": obj.get("status") or ("payment_failed" if failed else ""),
            "amount_due": obj.get("amount_due") or 0,
            "amount_paid": obj.get("amount_paid") or 0,
            "currency": obj.get("currency") or "usd",
            "hosted_invoice_url": obj.get("hosted_invoice_url") or "",
            "period_start": _ts(period.get("start") or obj.get("period_start")),
            "period_end": _ts(period.get("end") or obj.get("period_end")),
            "provider_created_at": _ts(obj.get("created")),
            "provider_payment_ref": obj.get("payment_intent") or obj.get("charge") or "",
        },
    )
    if failed:
        _audit("invoice_payment_failed", user, invoice=inv_id, subscription=sub_ref)
        if subscription and not subscription.past_due_since:
            subscription.past_due_since = timezone.now()
            subscription.save(update_fields=["past_due_since"])
    return True


def handle_refund(obj, event_type):
    """Record refunds against invoices. Refunds do not revoke access by themselves; cancellation does."""
    if event_type == "charge.refunded":
        refunded = obj.get("amount_refunded") or 0
        refs = [obj.get("invoice"), obj.get("payment_intent"), obj.get("id")]
    else:
        refunded = obj.get("amount") or 0
        refs = [obj.get("payment_intent"), obj.get("charge")]
    refs = [r for r in refs if r]
    invoice = Invoice.objects.filter(provider_invoice_id__in=refs).first() or Invoice.objects.filter(provider_payment_ref__in=refs).first()
    if invoice is None:
        logger.warning("Refund for unknown invoice (%s)", refs)
        return False
    if event_type == "charge.refunded":
        invoice.amount_refunded = refunded
    else:
        invoice.amount_refunded = max(invoice.amount_refunded, refunded)
    invoice.save(update_fields=["amount_refunded", "updated_at"])
    _audit("refund_recorded", invoice.user, invoice=invoice.provider_invoice_id, amount=refunded)
    return True
