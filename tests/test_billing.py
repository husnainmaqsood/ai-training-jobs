import time
from datetime import timedelta
from unittest import mock

import pytest
from django.urls import reverse
from django.utils import timezone

from billing import entitlements
from billing.models import BillingCustomer, Invoice, Subscription, WebhookEvent
from tests.conftest import stripe_signed

pytestmark = pytest.mark.django_db
WEBHOOK = "/billing/webhooks/stripe/"


def sub_obj(user, status="active", price="price_monthly_test", sub_id="sub_123", cancel=False, period_days=30, **extra):
    now = int(time.time())
    obj = {
        "id": sub_id,
        "object": "subscription",
        "customer": "cus_123",
        "status": status,
        "cancel_at_period_end": cancel,
        "metadata": {"user_id": str(user.pk)},
        "items": {"data": [{"price": {"id": price}, "current_period_start": now - 3600, "current_period_end": now + period_days * 86400}]},
    }
    obj.update(extra)
    return obj


def event(etype, obj, eid="evt_1", created=None):
    return {"id": eid, "object": "event", "type": etype, "created": created or int(time.time()), "data": {"object": obj}}


def post_event(client, payload, secret="whsec_test_secret"):
    body, sig = stripe_signed(payload, secret)
    return client.post(WEBHOOK, data=body, content_type="application/json", HTTP_STRIPE_SIGNATURE=sig)


# --- Checkout --------------------------------------------------------------------------------------


def fake_stripe_client():
    client = mock.MagicMock()
    client.v1.customers.create.return_value = mock.Mock(id="cus_new")
    client.v1.checkout.sessions.create.return_value = mock.Mock(url="https://checkout.stripe.com/c/pay/cs_test_1")
    client.v1.billing_portal.sessions.create.return_value = mock.Mock(url="https://billing.stripe.com/p/session/1")
    return client


def test_checkout_session_creation(client_user, user, plans):
    fake = fake_stripe_client()
    with mock.patch("stripe.StripeClient", return_value=fake):
        resp = client_user.post(reverse("billing:checkout", args=["premium_monthly"]))
    assert resp.status_code == 302 and resp["Location"].startswith("https://checkout.stripe.com/")
    params = fake.v1.checkout.sessions.create.call_args.kwargs["params"]
    assert params["mode"] == "subscription" and params["line_items"][0]["price"] == "price_monthly_test"
    assert params["client_reference_id"] == str(user.pk) and params["customer"] == "cus_new"
    assert BillingCustomer.objects.get(user=user).provider_customer_id == "cus_new"
    # nothing is granted until a verified webhook arrives
    assert entitlements.plan_for(user).code == "free"


def test_checkout_requires_post_verified_email_and_no_existing_sub(client, user, plans, subscription):
    client.force_login(user)
    assert client.get(reverse("billing:checkout", args=["premium_monthly"])).status_code == 405
    with mock.patch("stripe.StripeClient") as sc:
        resp = client.post(reverse("billing:checkout", args=["premium_annual"]))
    assert resp["Location"] == reverse("billing:settings") and not sc.called  # existing subscribers use the portal


def test_checkout_unconfigured_is_graceful(client_user, settings, plans):
    settings.STRIPE_SECRET_KEY = ""
    resp = client_user.post(reverse("billing:checkout", args=["premium_monthly"]))
    assert resp.status_code == 302 and resp["Location"] == reverse("billing:pricing")


def test_success_page_does_not_grant_access(client_user, user, plans):
    client_user.get(reverse("billing:success") + "?session_id=cs_fake")
    assert entitlements.plan_for(user).code == "free"


# --- Webhooks ----------------------------------------------------------------------------------------


def test_webhook_rejects_bad_or_missing_signature(client, user, plans):
    payload = event("customer.subscription.created", sub_obj(user))
    assert post_event(client, payload, secret="whsec_wrong").status_code == 400
    assert client.post(WEBHOOK, data="{}", content_type="application/json").status_code == 400
    assert not Subscription.objects.exists() and not WebhookEvent.objects.exists()


def test_subscription_lifecycle(client, user, plans):
    assert post_event(client, event("customer.subscription.created", sub_obj(user), "evt_a")).status_code == 200
    sub = Subscription.objects.get()
    assert sub.status == "active" and sub.plan.code == "premium_monthly"
    assert entitlements.plan_for(user).code == "premium_monthly"
    assert entitlements.quota_status(user).limit == 30

    # upgrade to annual via portal
    post_event(client, event("customer.subscription.updated", sub_obj(user, price="price_annual_test", period_days=365), "evt_b"))
    assert entitlements.plan_for(user).code == "premium_annual"

    # cancel at period end: access retained until period end
    post_event(client, event("customer.subscription.updated", sub_obj(user, price="price_annual_test", cancel=True), "evt_c"))
    sub.refresh_from_db()
    assert sub.cancel_at_period_end and entitlements.plan_for(user).code == "premium_annual"

    # subscription ends
    post_event(client, event("customer.subscription.deleted", sub_obj(user, status="canceled", price="price_annual_test"), "evt_d"))
    assert entitlements.plan_for(user).code == "free"


def test_duplicate_webhook_processed_once(client, user, plans):
    payload = event("customer.subscription.created", sub_obj(user), "evt_dup")
    with mock.patch("billing.webhooks.sync_subscription", wraps=__import__("billing.webhooks", fromlist=["x"]).sync_subscription) as spy:
        assert post_event(client, payload).status_code == 200
        assert post_event(client, payload).status_code == 200
    assert spy.call_count == 1
    assert WebhookEvent.objects.get(event_id="evt_dup").status == "processed"


def test_out_of_order_events_ignored(client, user, plans):
    now = int(time.time())
    post_event(client, event("customer.subscription.updated", sub_obj(user, status="canceled"), "evt_new", created=now))
    post_event(client, event("customer.subscription.created", sub_obj(user, status="active"), "evt_old", created=now - 60))
    assert Subscription.objects.get().status == "canceled"
    assert entitlements.plan_for(user).code == "free"


def test_failed_payment_grace_period(client, user, plans, settings):
    settings.BILLING_PAST_DUE_GRACE_DAYS = 3
    post_event(client, event("customer.subscription.created", sub_obj(user), "e1"))
    inv = {
        "id": "in_1",
        "customer": "cus_123",
        "metadata": {"user_id": str(user.pk)},
        "subscription": "sub_123",
        "status": "open",
        "amount_due": 1200,
        "amount_paid": 0,
        "currency": "usd",
        "created": int(time.time()),
    }
    post_event(client, event("invoice.payment_failed", inv, "e2"))
    post_event(client, event("customer.subscription.updated", sub_obj(user, status="past_due"), "e3"))
    sub = Subscription.objects.get()
    assert sub.status == "past_due" and sub.past_due_since
    assert entitlements.plan_for(user).code == "premium_monthly"  # within grace
    sub.past_due_since = timezone.now() - timedelta(days=4)
    sub.save()
    assert entitlements.plan_for(user).code == "free"  # grace expired
    # payment recovers
    post_event(client, event("customer.subscription.updated", sub_obj(user, status="active"), "e4"))
    assert entitlements.plan_for(user).code == "premium_monthly"
    assert Subscription.objects.get().past_due_since is None


def test_incomplete_and_unpaid_have_no_access(client, user, plans):
    post_event(client, event("customer.subscription.created", sub_obj(user, status="incomplete"), "e1"))
    assert entitlements.plan_for(user).code == "free"
    post_event(client, event("customer.subscription.updated", sub_obj(user, status="unpaid"), "e2"))
    assert entitlements.plan_for(user).code == "free"


def test_invoice_and_refund_recorded(client, user, plans):
    post_event(client, event("customer.subscription.created", sub_obj(user), "e1"))
    inv = {
        "id": "in_9",
        "number": "INV-9",
        "customer": "cus_123",
        "subscription": "sub_123",
        "status": "paid",
        "amount_due": 1200,
        "amount_paid": 1200,
        "currency": "usd",
        "payment_intent": "pi_9",
        "created": int(time.time()),
        "metadata": {"user_id": str(user.pk)},
    }
    post_event(client, event("invoice.paid", inv, "e2"))
    post_event(client, event("charge.refunded", {"id": "ch_9", "payment_intent": "pi_9", "amount_refunded": 1200}, "e3"))
    invoice = Invoice.objects.get()
    assert invoice.amount_paid == 1200 and invoice.amount_refunded == 1200
    assert entitlements.plan_for(user).code == "premium_monthly"  # refund alone doesn't cancel; provider cancellation does
    from core.models import AuditEvent

    assert AuditEvent.objects.filter(category="billing", action="refund_recorded").exists()


def test_webhook_failure_returns_500_for_retry(client, user, plans):
    payload = event("customer.subscription.created", sub_obj(user, price="price_unknown"), "e_fail")
    assert post_event(client, payload).status_code == 500
    ev = WebhookEvent.objects.get(event_id="e_fail")
    assert ev.status == "failed" and "No plan configured" in ev.error
    # once fixed, the retry is processed
    from billing.models import Plan

    Plan.objects.filter(code="premium_monthly").update(provider_price_id="price_unknown")
    assert post_event(client, payload).status_code == 200
    assert WebhookEvent.objects.get(event_id="e_fail").status == "processed"


def test_checkout_completed_links_customer(client, user, plans):
    session = {
        "id": "cs_1",
        "mode": "subscription",
        "customer": "cus_777",
        "subscription": "sub_777",
        "client_reference_id": str(user.pk),
        "metadata": {"user_id": str(user.pk)},
    }
    with mock.patch("billing.providers.StripeProvider.fetch_subscription", return_value=sub_obj(user, sub_id="sub_777", customer="cus_777")):
        assert post_event(client, event("checkout.session.completed", session, "e_cs")).status_code == 200
    assert BillingCustomer.objects.get(user=user).provider_customer_id == "cus_777"
    assert Subscription.objects.get(provider_subscription_id="sub_777").status == "active"


def test_cancel_view_calls_provider_but_waits_for_webhook(client_user, user, subscription):
    with mock.patch("billing.providers.StripeProvider.cancel_at_period_end") as cancel:
        client_user.post(reverse("billing:cancel"))
    cancel.assert_called_once()
    subscription.refresh_from_db()
    assert not subscription.cancel_at_period_end  # only the webhook changes local state


def test_portal_redirect(client_user, user, subscription):
    BillingCustomer.objects.create(user=user, provider_customer_id="cus_1")
    fake = fake_stripe_client()
    with mock.patch("stripe.StripeClient", return_value=fake):
        resp = client_user.post(reverse("billing:portal"))
    assert resp["Location"].startswith("https://billing.stripe.com/")


def test_reconciliation_repairs_drift(user, subscription):
    from billing.reconcile import reconcile_subscriptions

    remote = sub_obj(user, sub_id="sub_test_1", status="canceled")
    with mock.patch("billing.providers.StripeProvider.fetch_subscription", return_value=remote):
        result = reconcile_subscriptions()
    assert result == {"checked": 1, "changed": 1}
    subscription.refresh_from_db()
    assert subscription.status == "canceled"


def test_pricing_page_shows_annual_savings(client, plans):
    resp = client.get(reverse("billing:pricing"))
    assert b"Save 33%" in resp.content


def test_plan_limits_are_admin_configurable(user, plans):
    plans["free"].generations_per_period = 7
    plans["free"].save()
    assert entitlements.quota_status(user).limit == 7
