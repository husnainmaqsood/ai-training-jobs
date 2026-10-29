from django.conf import settings
from django.db import models
from django.db.models import Q


class Plan(models.Model):
    """Prices, quotas and entitlements. Fully editable in the admin — no code changes required."""

    class Interval(models.TextChoices):
        NONE = "none", "Free / no billing"
        MONTH = "month", "Monthly"
        YEAR = "year", "Yearly"

    code = models.SlugField(max_length=50, unique=True)
    name = models.CharField(max_length=100)
    tagline = models.CharField(max_length=200, blank=True)
    price_cents = models.PositiveIntegerField(default=0)
    currency = models.CharField(max_length=3, default="USD")
    interval = models.CharField(max_length=10, choices=Interval.choices, default=Interval.NONE)
    provider_price_id = models.CharField(max_length=200, blank=True, help_text="Payment-provider price ID (e.g. Stripe price_...).")

    # Quotas / limits
    generations_per_period = models.PositiveIntegerField(default=2)
    max_master_cvs = models.PositiveIntegerField(default=1)
    max_saved_jobs = models.PositiveIntegerField(default=20)
    max_applications = models.PositiveIntegerField(default=10)
    max_generated_cvs_visible = models.PositiveIntegerField(default=5, help_text="How many past generations are visible in history.")
    section_regenerations_per_cv = models.PositiveIntegerField(default=0)

    # Feature entitlements
    can_export_docx = models.BooleanField(default=False)
    can_export_pdf = models.BooleanField(default=True)
    detailed_fit_analysis = models.BooleanField(default=False)
    can_edit_generated = models.BooleanField(default=True)
    premium_tracking = models.BooleanField(default=False)

    marketing_features = models.JSONField(default=list, blank=True, help_text="List of bullet strings for the pricing page.")
    is_default_free = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    is_public = models.BooleanField(default=True)
    highlight = models.BooleanField(default=False)
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "price_cents"]
        constraints = [models.UniqueConstraint(fields=["is_default_free"], condition=Q(is_default_free=True), name="single_default_free_plan")]

    def __str__(self):
        return self.name

    @property
    def is_free(self):
        return self.interval == self.Interval.NONE or self.price_cents == 0

    @property
    def price_display(self):
        amount = self.price_cents / 100
        return f"${amount:,.0f}" if amount == int(amount) else f"${amount:,.2f}"

    @property
    def monthly_equivalent_cents(self):
        if self.interval == self.Interval.YEAR:
            return round(self.price_cents / 12)
        return self.price_cents


class BillingCustomer(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="billing_customer")
    provider = models.CharField(max_length=30, default="stripe")
    provider_customer_id = models.CharField(max_length=200, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user} ({self.provider_customer_id})"


class Subscription(models.Model):
    """Mirror of the provider's subscription. Only webhook/provider-verified data writes here."""

    class Status(models.TextChoices):
        INCOMPLETE = "incomplete", "Incomplete"
        INCOMPLETE_EXPIRED = "incomplete_expired", "Incomplete (expired)"
        TRIALING = "trialing", "Trialing"
        ACTIVE = "active", "Active"
        PAST_DUE = "past_due", "Past due"
        CANCELED = "canceled", "Canceled"
        UNPAID = "unpaid", "Unpaid"
        PAUSED = "paused", "Paused"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="subscriptions")
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="subscriptions")
    provider = models.CharField(max_length=30, default="stripe")
    provider_subscription_id = models.CharField(max_length=200, unique=True)
    status = models.CharField(max_length=30, choices=Status.choices, db_index=True)
    current_period_start = models.DateTimeField(null=True, blank=True)
    current_period_end = models.DateTimeField(null=True, blank=True)
    cancel_at_period_end = models.BooleanField(default=False)
    canceled_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    past_due_since = models.DateTimeField(null=True, blank=True)
    last_event_at = models.DateTimeField(
        null=True, blank=True, help_text="Provider timestamp of the most recent applied event (out-of-order protection)."
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user} — {self.plan} [{self.status}]"


class Invoice(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="invoices")
    subscription = models.ForeignKey(Subscription, null=True, blank=True, on_delete=models.SET_NULL, related_name="invoices")
    provider = models.CharField(max_length=30, default="stripe")
    provider_invoice_id = models.CharField(max_length=200, unique=True)
    number = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=30)
    amount_due = models.PositiveIntegerField(default=0)
    amount_paid = models.PositiveIntegerField(default=0)
    amount_refunded = models.PositiveIntegerField(default=0)
    currency = models.CharField(max_length=3, default="usd")
    hosted_invoice_url = models.URLField(max_length=1000, blank=True)
    period_start = models.DateTimeField(null=True, blank=True)
    period_end = models.DateTimeField(null=True, blank=True)
    provider_created_at = models.DateTimeField(null=True, blank=True)
    provider_payment_ref = models.CharField(max_length=200, blank=True, help_text="Charge / payment intent ID.")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-provider_created_at"]

    def __str__(self):
        return f"{self.number or self.provider_invoice_id} {self.status}"


class WebhookEvent(models.Model):
    class Status(models.TextChoices):
        RECEIVED = "received", "Received"
        PROCESSED = "processed", "Processed"
        IGNORED = "ignored", "Ignored"
        FAILED = "failed", "Failed"

    provider = models.CharField(max_length=30)
    event_id = models.CharField(max_length=200)
    event_type = models.CharField(max_length=100, db_index=True)
    provider_created_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RECEIVED)
    attempts = models.PositiveSmallIntegerField(default=0)
    error = models.TextField(blank=True)
    payload = models.JSONField(default=dict)
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-received_at"]
        constraints = [models.UniqueConstraint(fields=["provider", "event_id"], name="unique_webhook_event")]

    def __str__(self):
        return f"{self.provider}:{self.event_type} {self.event_id} [{self.status}]"


class UsageCounter(models.Model):
    """One row per user; locked with SELECT ... FOR UPDATE when reserving generation quota."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="usage_counter")
    period_start = models.DateTimeField()
    period_end = models.DateTimeField()
    generations_used = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user}: {self.generations_used} used ({self.period_start:%Y-%m-%d}–{self.period_end:%Y-%m-%d})"


class UsageLedgerEntry(models.Model):
    """Immutable record of every quota reservation/refund for audit and reconciliation."""

    class Kind(models.TextChoices):
        CONSUME = "consume", "Consumed"
        REFUND = "refund", "Refunded"
        ADJUST = "adjust", "Admin adjustment"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="usage_ledger")
    generation = models.ForeignKey("cvs.GenerationRequest", null=True, blank=True, on_delete=models.SET_NULL)
    kind = models.CharField(max_length=10, choices=Kind.choices)
    amount = models.IntegerField()
    period_start = models.DateTimeField()
    reason = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user} {self.kind} {self.amount}"


class CreditAccount(models.Model):
    """CV-builder credit balance. Locked with SELECT ... FOR UPDATE whenever it changes."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="credit_account")
    balance = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user}: {self.balance} credits"


class CreditTransaction(models.Model):
    """Immutable ledger of every credit change. `reference` makes purchases idempotent."""

    class Kind(models.TextChoices):
        PURCHASE = "purchase", "Purchase"
        TEST_GRANT = "test_grant", "Test-mode grant (no payment)"
        CONSUME = "consume", "Used for a CV"
        REFUND = "refund", "Refunded (generation failed)"
        ADJUST = "adjust", "Admin adjustment"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="credit_transactions")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    amount = models.IntegerField(help_text="Positive adds credits, negative uses them.")
    balance_after = models.PositiveIntegerField()
    reference = models.CharField(max_length=200, blank=True, help_text="Payment session id or generation id.")
    note = models.CharField(max_length=200, blank=True)
    amount_paid_cents = models.PositiveIntegerField(default=0)
    currency = models.CharField(max_length=3, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["kind", "reference"], condition=~Q(reference=""), name="unique_credit_reference_per_kind"),
        ]

    def __str__(self):
        return f"{self.user} {self.kind} {self.amount:+d}"
