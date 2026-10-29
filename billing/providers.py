"""
Payment-provider abstraction. Views and business logic depend only on `BillingProvider`; Stripe specifics
live in `StripeProvider`. To add another provider (e.g. Paddle or Lemon Squeezy as a merchant of record
where Stripe is unavailable), implement this interface and a matching webhook translator.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from django.conf import settings


class BillingNotConfigured(Exception):
    pass


class InvalidWebhook(Exception):
    pass


class BillingProviderError(Exception):
    pass


@dataclass
class WebhookPayload:
    id: str
    type: str
    created: int
    data: dict
    raw: dict


class BillingProvider(ABC):
    name = "base"

    @abstractmethod
    def is_configured(self) -> bool: ...

    @abstractmethod
    def create_checkout_url(self, *, user, plan, success_url, cancel_url) -> str: ...

    @abstractmethod
    def create_portal_url(self, *, user, return_url) -> str: ...

    @abstractmethod
    def cancel_at_period_end(self, subscription) -> None: ...

    @abstractmethod
    def resume(self, subscription) -> None: ...

    @abstractmethod
    def cancel_immediately(self, subscription) -> None: ...

    @abstractmethod
    def verify_webhook(self, payload: bytes, headers) -> WebhookPayload: ...

    @abstractmethod
    def fetch_subscription(self, provider_subscription_id: str) -> dict: ...

    def create_credit_checkout_url(self, *, user, pack, success_url, cancel_url) -> str:
        raise BillingNotConfigured(f"{self.name} does not support one-time credit purchases.")


class StripeProvider(BillingProvider):
    name = "stripe"

    def __init__(self):
        self.secret_key = settings.STRIPE_SECRET_KEY
        self.webhook_secret = settings.STRIPE_WEBHOOK_SECRET

    def is_configured(self):
        return bool(self.secret_key and self.webhook_secret)

    def _client(self):
        import stripe

        if not self.secret_key:
            raise BillingNotConfigured("STRIPE_SECRET_KEY is not set.")
        return stripe.StripeClient(self.secret_key, max_network_retries=2)

    def _call(self, fn, *args, **kwargs):
        import stripe

        try:
            return fn(*args, **kwargs)
        except stripe.StripeError as exc:  # never leak provider details to users
            raise BillingProviderError(getattr(exc, "user_message", None) or "Payment provider error.") from exc

    def _customer_id(self, user):
        from .models import BillingCustomer

        existing = BillingCustomer.objects.filter(user=user).first()
        if existing:
            return existing.provider_customer_id
        client = self._client()
        customer = self._call(
            client.v1.customers.create,
            params={"email": user.email, "metadata": {"user_id": str(user.pk)}},
            options={"idempotency_key": f"customer-{user.pk}"},
        )
        BillingCustomer.objects.get_or_create(user=user, defaults={"provider": self.name, "provider_customer_id": customer.id})
        return BillingCustomer.objects.get(user=user).provider_customer_id

    def create_checkout_url(self, *, user, plan, success_url, cancel_url):
        if not plan.provider_price_id:
            raise BillingNotConfigured(f"Plan '{plan.code}' has no provider price ID configured.")
        client = self._client()
        session = self._call(
            client.v1.checkout.sessions.create,
            params={
                "mode": "subscription",
                "customer": self._customer_id(user),
                "client_reference_id": str(user.pk),
                "line_items": [{"price": plan.provider_price_id, "quantity": 1}],
                "success_url": success_url + "?session_id={CHECKOUT_SESSION_ID}",
                "cancel_url": cancel_url,
                "allow_promotion_codes": True,
                "subscription_data": {"metadata": {"user_id": str(user.pk), "plan_code": plan.code}},
                "metadata": {"user_id": str(user.pk), "plan_code": plan.code},
            },
        )
        return session.url

    def create_credit_checkout_url(self, *, user, pack, success_url, cancel_url):
        """One-time payment for a credit pack. Credits are granted only by the verified webhook."""
        client = self._client()
        metadata = {"user_id": str(user.pk), "kind": "cv_credits", "pack": pack.code, "credits": str(pack.credits)}
        session = self._call(
            client.v1.checkout.sessions.create,
            params={
                "mode": "payment",
                "customer": self._customer_id(user),
                "client_reference_id": str(user.pk),
                "line_items": [
                    {
                        "quantity": 1,
                        "price_data": {
                            "currency": settings.CREDIT_CURRENCY,
                            "unit_amount": pack.price_cents,
                            "product_data": {"name": f"{pack.credits} CV credit{'s' if pack.credits != 1 else ''} ({pack.label})"},
                        },
                    }
                ],
                "success_url": success_url + "?session_id={CHECKOUT_SESSION_ID}",
                "cancel_url": cancel_url,
                "metadata": metadata,
                "payment_intent_data": {"metadata": metadata},
            },
        )
        return session.url

    def create_portal_url(self, *, user, return_url):
        client = self._client()
        session = self._call(
            client.v1.billing_portal.sessions.create,
            params={"customer": self._customer_id(user), "return_url": return_url},
        )
        return session.url

    def cancel_at_period_end(self, subscription):
        client = self._client()
        self._call(
            client.v1.subscriptions.update,
            subscription.provider_subscription_id,
            params={"cancel_at_period_end": True},
        )

    def resume(self, subscription):
        client = self._client()
        self._call(
            client.v1.subscriptions.update,
            subscription.provider_subscription_id,
            params={"cancel_at_period_end": False},
        )

    def cancel_immediately(self, subscription):
        client = self._client()
        self._call(client.v1.subscriptions.cancel, subscription.provider_subscription_id)

    def verify_webhook(self, payload, headers):
        import stripe

        if not self.webhook_secret:
            raise BillingNotConfigured("STRIPE_WEBHOOK_SECRET is not set.")
        signature = headers.get("Stripe-Signature") or headers.get("stripe-signature")
        if not signature:
            raise InvalidWebhook("Missing signature header")
        try:
            event = stripe.Webhook.construct_event(payload, signature, self.webhook_secret)
        except (ValueError, stripe.SignatureVerificationError) as exc:
            raise InvalidWebhook(str(exc)) from exc
        raw = event.to_dict() if hasattr(event, "to_dict") else dict(event)
        return WebhookPayload(id=raw["id"], type=raw["type"], created=int(raw.get("created") or 0), data=raw.get("data", {}), raw=raw)

    def fetch_subscription(self, provider_subscription_id):
        client = self._client()
        sub = self._call(client.v1.subscriptions.retrieve, provider_subscription_id)
        return sub.to_dict() if hasattr(sub, "to_dict") else dict(sub)


_PROVIDERS = {"stripe": StripeProvider}


def get_provider() -> BillingProvider:
    cls = _PROVIDERS.get(settings.BILLING_PROVIDER)
    if cls is None:
        raise BillingNotConfigured(f"Unknown BILLING_PROVIDER '{settings.BILLING_PROVIDER}'")
    return cls()
