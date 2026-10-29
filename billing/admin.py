from django.contrib import admin, messages

from core.admin_mixins import AuditedAdminMixin

from .models import BillingCustomer, CreditAccount, CreditTransaction, Invoice, Plan, Subscription, UsageCounter, UsageLedgerEntry, WebhookEvent
from .providers import get_provider
from .webhooks import process_event


@admin.register(Plan)
class PlanAdmin(AuditedAdminMixin, admin.ModelAdmin):
    list_display = (
        "name",
        "code",
        "price_display",
        "interval",
        "generations_per_period",
        "is_active",
        "is_public",
        "is_default_free",
        "provider_price_id",
    )
    list_editable = ("is_active", "is_public")
    fieldsets = (
        (None, {"fields": ("code", "name", "tagline", "is_active", "is_public", "is_default_free", "highlight", "sort_order")}),
        (
            "Price",
            {
                "fields": ("price_cents", "currency", "interval", "provider_price_id"),
                "description": "Changing the price here changes what we display. The amount actually charged is the provider price "
                "(provider_price_id) — create a new price in Stripe and paste its ID when you change pricing.",
            },
        ),
        (
            "Limits",
            {
                "fields": (
                    "generations_per_period",
                    "max_master_cvs",
                    "max_saved_jobs",
                    "max_applications",
                    "max_generated_cvs_visible",
                    "section_regenerations_per_cv",
                )
            },
        ),
        ("Entitlements", {"fields": ("can_export_pdf", "can_export_docx", "detailed_fit_analysis", "can_edit_generated", "premium_tracking")}),
        ("Pricing page", {"fields": ("marketing_features",)}),
    )


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ("user", "plan", "status", "current_period_end", "cancel_at_period_end", "last_event_at")
    list_filter = ("status", "plan", "cancel_at_period_end")
    search_fields = ("user__email", "provider_subscription_id")
    raw_id_fields = ("user",)
    readonly_fields = (
        "provider_subscription_id",
        "status",
        "current_period_start",
        "current_period_end",
        "cancel_at_period_end",
        "canceled_at",
        "ended_at",
        "past_due_since",
        "last_event_at",
        "created_at",
        "updated_at",
    )
    actions = ["resync_from_provider"]

    @admin.action(description="Re-sync from payment provider")
    def resync_from_provider(self, request, queryset):
        import time

        from .webhooks import sync_subscription

        provider = get_provider()
        for sub in queryset:
            try:
                sync_subscription(provider.fetch_subscription(sub.provider_subscription_id), int(time.time()))
            except Exception as exc:
                self.message_user(request, f"{sub}: {exc}", messages.ERROR)
        self.message_user(request, "Re-sync complete.")


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ("provider_created_at", "user", "number", "status", "amount_paid", "amount_refunded", "currency")
    list_filter = ("status",)
    search_fields = ("user__email", "provider_invoice_id", "number")
    readonly_fields = [f.name for f in Invoice._meta.fields]


@admin.register(WebhookEvent)
class WebhookEventAdmin(admin.ModelAdmin):
    list_display = ("received_at", "provider", "event_type", "event_id", "status", "attempts")
    list_filter = ("status", "event_type", "provider")
    search_fields = ("event_id",)
    readonly_fields = [f.name for f in WebhookEvent._meta.fields]
    actions = ["reprocess"]

    @admin.action(description="Re-process failed events")
    def reprocess(self, request, queryset):
        from .providers import WebhookPayload

        for ev in queryset.filter(status=WebhookEvent.Status.FAILED):
            raw = ev.payload
            ev.status = WebhookEvent.Status.RECEIVED
            ev.save(update_fields=["status"])
            process_event(
                ev.provider, WebhookPayload(id=raw["id"], type=raw["type"], created=int(raw.get("created") or 0), data=raw.get("data", {}), raw=raw)
            )
        self.message_user(request, "Re-processed.")


@admin.register(BillingCustomer)
class BillingCustomerAdmin(admin.ModelAdmin):
    list_display = ("user", "provider", "provider_customer_id", "created_at")
    search_fields = ("user__email", "provider_customer_id")
    raw_id_fields = ("user",)


@admin.register(UsageCounter)
class UsageCounterAdmin(AuditedAdminMixin, admin.ModelAdmin):
    list_display = ("user", "generations_used", "period_start", "period_end")
    search_fields = ("user__email",)
    raw_id_fields = ("user",)


@admin.register(UsageLedgerEntry)
class UsageLedgerAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "kind", "amount", "reason")
    list_filter = ("kind",)
    search_fields = ("user__email",)
    readonly_fields = [f.name for f in UsageLedgerEntry._meta.fields]


@admin.register(CreditAccount)
class CreditAccountAdmin(admin.ModelAdmin):
    """Read-only: change balances with billing.credits.grant(..., kind="adjust") so the ledger stays complete."""

    list_display = ("user", "balance", "updated_at")
    search_fields = ("user__email",)
    readonly_fields = [f.name for f in CreditAccount._meta.fields]

    def has_add_permission(self, request):
        return False


@admin.register(CreditTransaction)
class CreditTransactionAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "kind", "amount", "balance_after", "amount_paid_cents", "reference")
    list_filter = ("kind",)
    search_fields = ("user__email", "reference")
    readonly_fields = [f.name for f in CreditTransaction._meta.fields]

    def has_add_permission(self, request):
        return False
