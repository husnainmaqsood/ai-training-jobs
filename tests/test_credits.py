from unittest import mock

import pytest
from django.urls import reverse

from billing import credits
from billing.models import CreditTransaction, WebhookEvent
from tests.test_billing import event, fake_stripe_client, post_event

pytestmark = pytest.mark.django_db


def credit_session(user, pack="starter", amount=800, paid="paid", sid="cs_credit_1"):
    return {
        "id": sid,
        "object": "checkout.session",
        "mode": "payment",
        "payment_status": paid,
        "amount_total": amount,
        "currency": "usd",
        "client_reference_id": str(user.pk),
        "metadata": {"user_id": str(user.pk), "kind": "cv_credits", "pack": pack, "credits": "999"},
    }


def test_consume_refund_and_grant_are_ledgered(user):
    with pytest.raises(credits.InsufficientCredits):
        credits.consume(user)
    credits.grant(user, 2, CreditTransaction.Kind.ADJUST, note="test")
    txn = credits.consume(user, reference="cv-1")
    assert credits.balance(user) == 1
    credits.refund(txn)
    credits.refund(txn)  # second refund is a no-op
    assert credits.balance(user) == 2
    kinds = list(CreditTransaction.objects.filter(user=user).order_by("created_at").values_list("kind", "amount", "balance_after"))
    assert kinds == [("adjust", 2, 2), ("consume", -1, 1), ("refund", 1, 2)]


def test_grant_with_reference_is_idempotent(user):
    assert credits.grant(user, 5, CreditTransaction.Kind.PURCHASE, reference="cs_1")
    assert credits.grant(user, 5, CreditTransaction.Kind.PURCHASE, reference="cs_1") is None
    assert credits.balance(user) == 5


def test_webhook_grants_pack_credits_once(client, user):
    payload = event("checkout.session.completed", credit_session(user), eid="evt_c1")
    assert post_event(client, payload).status_code == 200
    # Credits come from our pack definition (5), never from metadata sent through the browser (999).
    assert credits.balance(user) == 5
    # Replayed event and a different event for the same session both grant nothing more.
    post_event(client, payload)
    post_event(client, event("checkout.session.async_payment_succeeded", credit_session(user), eid="evt_c2"))
    assert credits.balance(user) == 5
    txn = CreditTransaction.objects.get(user=user, kind="purchase")
    assert txn.reference == "cs_credit_1" and txn.amount_paid_cents == 800


def test_webhook_ignores_unpaid_and_rejects_underpaid_sessions(client, user):
    post_event(client, event("checkout.session.completed", credit_session(user, paid="unpaid"), eid="evt_u"))
    assert credits.balance(user) == 0
    post_event(client, event("checkout.session.completed", credit_session(user, amount=100, sid="cs_low"), eid="evt_low"))
    assert credits.balance(user) == 0
    assert WebhookEvent.objects.get(event_id="evt_low").status == WebhookEvent.Status.FAILED


def test_buy_in_test_mode_grants_without_payment(client_user, user, settings):
    settings.STRIPE_SECRET_KEY = ""
    settings.BILLING_TEST_MODE = True
    resp = client_user.post(reverse("cvs:buy_credits", args=["starter"]))
    assert resp.status_code == 302
    assert credits.balance(user) == 5
    assert CreditTransaction.objects.get(user=user).kind == CreditTransaction.Kind.TEST_GRANT


def test_buy_disabled_when_payments_off(client_user, user, settings):
    settings.STRIPE_SECRET_KEY = ""
    settings.BILLING_TEST_MODE = False
    client_user.post(reverse("cvs:buy_credits", args=["starter"]))
    assert credits.balance(user) == 0
    page = client_user.get(reverse("cvs:builder")).content
    assert b"payments aren" in page


def test_buy_with_stripe_redirects_to_checkout_and_grants_nothing(client_user, user):
    fake = fake_stripe_client()
    with mock.patch("stripe.StripeClient", return_value=fake):
        resp = client_user.post(reverse("cvs:buy_credits", args=["pro"]))
    assert resp.status_code == 302 and resp["Location"].startswith("https://checkout.stripe.com/")
    params = fake.v1.checkout.sessions.create.call_args.kwargs["params"]
    assert params["mode"] == "payment"
    assert params["line_items"][0]["price_data"]["unit_amount"] == 2000
    assert params["metadata"]["kind"] == "cv_credits" and params["metadata"]["pack"] == "pro"
    assert credits.balance(user) == 0
    # the browser coming back is not proof of payment
    client_user.get(reverse("cvs:builder_purchase_done"))
    assert credits.balance(user) == 0


def test_buy_requires_login_and_post(client, client_user):
    url = reverse("cvs:buy_credits", args=["starter"])
    assert client_user.get(url).status_code == 405
    client.logout()
    assert client.post(url).status_code == 302


def test_payment_mode_labels(settings):
    settings.STRIPE_SECRET_KEY, settings.STRIPE_WEBHOOK_SECRET = "sk_test_x", "whsec_x"
    assert credits.payment_mode() == "stripe_test"
    settings.STRIPE_SECRET_KEY = "sk_live_x"
    assert credits.payment_mode() == "live"
    settings.STRIPE_SECRET_KEY, settings.BILLING_TEST_MODE = "", True
    assert credits.payment_mode() == "test"
    settings.BILLING_TEST_MODE = False
    assert credits.payment_mode() == "off"
