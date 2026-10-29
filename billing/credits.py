"""
CV-builder credits. All balance changes happen here, inside a transaction that locks the user's
CreditAccount row, and each one writes a CreditTransaction.

Policy:
* One CV generation (either mode) costs one credit, reserved before the AI is called.
* If the generation fails for a system reason, the credit is refunded automatically, exactly once.
* Preview, editing and downloads of a generated CV are free.
* Credits are only added by a verified payment webhook, an admin, or — in test mode only — a test grant.
"""

from dataclasses import dataclass

from django.conf import settings
from django.db import IntegrityError, transaction

from .models import CreditAccount, CreditTransaction


class InsufficientCredits(Exception):
    pass


@dataclass(frozen=True)
class CreditPack:
    code: str
    credits: int
    price_cents: int
    label: str

    @property
    def price_display(self):
        amount = self.price_cents / 100
        return f"${amount:,.0f}" if amount == int(amount) else f"${amount:,.2f}"

    @property
    def per_credit_display(self):
        return f"${self.price_cents / 100 / self.credits:,.2f}"


def packs():
    return [
        CreditPack(code=str(p["code"]), credits=int(p["credits"]), price_cents=int(p["price_cents"]), label=str(p.get("label") or p["code"]))
        for p in settings.CREDIT_PACKS
        if int(p.get("credits", 0)) > 0
    ]


def get_pack(code):
    return next((p for p in packs() if p.code == code), None)


def balance(user):
    if not user.is_authenticated:
        return 0
    account = CreditAccount.objects.filter(user=user).first()
    return account.balance if account else 0


def _locked_account(user):
    try:
        CreditAccount.objects.get_or_create(user=user)
    except IntegrityError:
        pass  # created concurrently
    return CreditAccount.objects.select_for_update().get(user=user)


def _apply(user, amount, kind, reference="", note="", amount_paid_cents=0, currency=""):
    with transaction.atomic():
        account = _locked_account(user)
        if amount < 0 and account.balance < -amount:
            raise InsufficientCredits("You don't have enough credits.")
        account.balance += amount
        account.save(update_fields=["balance", "updated_at"])
        return CreditTransaction.objects.create(
            user=user,
            kind=kind,
            amount=amount,
            balance_after=account.balance,
            reference=reference[:200],
            note=note[:200],
            amount_paid_cents=amount_paid_cents,
            currency=currency[:3],
        )


def consume(user, reference="", note=""):
    """Reserve one credit. Raises InsufficientCredits."""
    return _apply(user, -1, CreditTransaction.Kind.CONSUME, reference, note)


def refund(txn, note="Generation failed"):
    """Give back the credit taken by `txn`, at most once."""
    if txn is None or txn.kind != CreditTransaction.Kind.CONSUME:
        return None
    try:
        return _apply(txn.user, 1, CreditTransaction.Kind.REFUND, reference=f"refund-{txn.pk}", note=note)
    except IntegrityError:
        return None  # already refunded


def grant(user, credits, kind, reference="", note="", amount_paid_cents=0, currency=""):
    """Add credits. With a reference, repeated calls (e.g. duplicate webhooks) grant only once."""
    if credits <= 0:
        raise ValueError("credits must be positive")
    if reference and CreditTransaction.objects.filter(kind=kind, reference=reference).exists():
        return None
    try:
        return _apply(user, credits, kind, reference, note, amount_paid_cents, currency)
    except IntegrityError:
        return None


def stripe_ready():
    from .providers import BillingNotConfigured, get_provider

    try:
        return get_provider().is_configured()
    except BillingNotConfigured:
        return False


def payment_mode():
    """'live', 'stripe_test', 'test' (free test credits) or 'off'."""
    if stripe_ready():
        return "stripe_test" if settings.STRIPE_SECRET_KEY.startswith(("sk_test_", "rk_test_")) else "live"
    if settings.BILLING_TEST_MODE:
        return "test"
    return "off"
