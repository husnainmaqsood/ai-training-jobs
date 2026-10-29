import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from core.audit import record
from core.models import AuditEvent
from core.ratelimit import ratelimit

from . import entitlements
from .models import Invoice, Plan, Subscription
from .providers import BillingNotConfigured, BillingProviderError, InvalidWebhook, get_provider
from .webhooks import process_event

logger = logging.getLogger(__name__)


def _billing_ready():
    try:
        return get_provider().is_configured()
    except BillingNotConfigured:
        return False


def pricing(request):
    plans = list(Plan.objects.filter(is_active=True, is_public=True))
    monthly = next((p for p in plans if p.interval == Plan.Interval.MONTH), None)
    for p in plans:
        p.savings_pct = None
        if monthly and p.interval == Plan.Interval.YEAR and monthly.price_cents:
            full_year = monthly.price_cents * 12
            if full_year > p.price_cents:
                p.savings_pct = round(100 * (full_year - p.price_cents) / full_year)
                p.savings_cents = full_year - p.price_cents
    current = entitlements.plan_for(request.user) if request.user.is_authenticated else None
    return render(
        request,
        "billing/pricing.html",
        {
            "plans": plans,
            "current_plan": current,
            "billing_ready": _billing_ready(),
            "meta_title": "Pricing",
            "meta_description": "Free and Premium plans for AI CV tailoring and job tracking.",
        },
    )


@login_required
def billing_settings(request):
    sub = entitlements.active_subscription(request.user)
    return render(
        request,
        "billing/settings.html",
        {
            "subscription": sub,
            "all_subscriptions": Subscription.objects.filter(user=request.user).select_related("plan")[:10],
            "invoices": Invoice.objects.filter(user=request.user)[:24],
            "quota": entitlements.quota_status(request.user),
            "billing_ready": _billing_ready(),
            "grace_days": settings.BILLING_PAST_DUE_GRACE_DAYS,
        },
    )


@login_required
@require_POST
@ratelimit("checkout", limit=10, window_seconds=600, by="user")
def checkout(request, code):
    plan = get_object_or_404(Plan, code=code, is_active=True)
    if plan.is_free:
        return redirect("billing:pricing")
    if not request.user.email_verified:
        messages.error(request, "Please verify your email address before subscribing.")
        return redirect("billing:pricing")
    if entitlements.active_subscription(request.user):
        # Plan changes for existing subscribers go through the provider portal (proration handled there).
        messages.info(request, "You already have a subscription. Use “Manage billing” to switch plans.")
        return redirect("billing:settings")
    try:
        provider = get_provider()
        url = provider.create_checkout_url(
            user=request.user,
            plan=plan,
            success_url=settings.SITE_URL + reverse("billing:success"),
            cancel_url=settings.SITE_URL + reverse("billing:cancelled"),
        )
    except BillingNotConfigured as exc:
        logger.warning("Checkout unavailable: %s", exc)
        messages.error(request, "Online payments are not configured yet. Please try again later.")
        return redirect("billing:pricing")
    except BillingProviderError as exc:
        messages.error(request, str(exc))
        return redirect("billing:pricing")
    record("checkout_started", actor=request.user, target=request.user, category=AuditEvent.Category.BILLING, plan=plan.code)
    return redirect(url)


@login_required
def checkout_success(request):
    # Never grant access here — the browser redirect is not proof of payment. Webhooks update the subscription.
    return render(request, "billing/success.html", {"subscription": entitlements.active_subscription(request.user)})


@login_required
def checkout_cancelled(request):
    return render(request, "billing/cancelled.html")


@login_required
@require_POST
def portal(request):
    try:
        url = get_provider().create_portal_url(user=request.user, return_url=settings.SITE_URL + reverse("billing:settings"))
    except (BillingNotConfigured, BillingProviderError):
        messages.error(request, "The billing portal is unavailable right now.")
        return redirect("billing:settings")
    return redirect(url)


@login_required
@require_POST
def cancel(request):
    sub = entitlements.active_subscription(request.user)
    if not sub:
        messages.info(request, "You don't have an active subscription.")
        return redirect("billing:settings")
    try:
        get_provider().cancel_at_period_end(sub)
    except (BillingNotConfigured, BillingProviderError):
        messages.error(request, "We couldn't reach the payment provider. Please try again or use Manage billing.")
        return redirect("billing:settings")
    record(
        "cancel_requested", actor=request.user, target=request.user, category=AuditEvent.Category.BILLING, subscription=sub.provider_subscription_id
    )
    messages.success(
        request, "Cancellation requested. You keep Premium until the end of the current billing period; this page updates once the provider confirms."
    )
    return redirect("billing:settings")


@login_required
@require_POST
def resume(request):
    sub = entitlements.active_subscription(request.user)
    if not sub or not sub.cancel_at_period_end:
        return redirect("billing:settings")
    try:
        get_provider().resume(sub)
    except (BillingNotConfigured, BillingProviderError):
        messages.error(request, "We couldn't reach the payment provider. Please try again.")
        return redirect("billing:settings")
    record("resume_requested", actor=request.user, target=request.user, category=AuditEvent.Category.BILLING)
    messages.success(request, "Renewal requested. This page updates once the provider confirms.")
    return redirect("billing:settings")


@csrf_exempt
@require_POST
def stripe_webhook(request):
    provider = get_provider()
    try:
        payload = provider.verify_webhook(request.body, request.headers)
    except BillingNotConfigured:
        return HttpResponse("Webhook not configured", status=503)
    except InvalidWebhook:
        return HttpResponseBadRequest("Invalid signature")
    event = process_event(provider.name, payload)
    if event.status == event.Status.FAILED:
        return HttpResponse("Processing failed", status=500)  # provider will retry
    return HttpResponse("ok")
