"""
Server-side plan resolution, entitlement checks and generation-quota accounting.

Quota policy (also documented in docs/BILLING.md):

* One generation = one full "tailor this CV to this job" run. It reserves 1 unit of quota at submission
  time, inside a transaction that locks the user's UsageCounter row, so concurrent requests cannot exceed
  the allowance.
* If the generation fails for a system reason (provider outage, timeout, invalid model output after
  retries, global safety limit), the unit is refunded automatically. A generation that succeeds is never
  refunded, even if the user later deletes it.
* Automatic retries inside one generation never consume extra quota.
* Section regenerations do not consume generation quota; they are limited per generated CV by the plan's
  `section_regenerations_per_cv`.
* Free plans use calendar-month periods (UTC). Paid plans use the provider's current billing period.
"""

import calendar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import Plan, Subscription, UsageCounter, UsageLedgerEntry

ACCESS_STATUSES = {Subscription.Status.ACTIVE, Subscription.Status.TRIALING}
# Tolerate delayed renewal webhooks: an active subscription keeps access this long past period end.
RENEWAL_LEEWAY = timedelta(hours=24)


class QuotaExceeded(Exception):
    pass


class FeatureNotAvailable(Exception):
    pass


def default_free_plan():
    plan = Plan.objects.filter(is_default_free=True, is_active=True).first()
    if plan is None:
        # Safe fallback if the admin has not seeded plans: a minimal free plan held in memory.
        plan = Plan(code="free", name="Free", generations_per_period=2, interval=Plan.Interval.NONE)
    return plan


def subscription_grants_access(sub, now=None):
    now = now or timezone.now()
    if sub.status in ACCESS_STATUSES:
        return sub.current_period_end is None or now < sub.current_period_end + RENEWAL_LEEWAY
    if sub.status == Subscription.Status.PAST_DUE:
        since = sub.past_due_since or sub.current_period_end or now
        return now < since + timedelta(days=settings.BILLING_PAST_DUE_GRACE_DAYS)
    return False


def active_subscription(user, now=None):
    if not user.is_authenticated:
        return None
    subs = (
        Subscription.objects.filter(user=user)
        .exclude(status__in=[Subscription.Status.CANCELED, Subscription.Status.INCOMPLETE_EXPIRED])
        .select_related("plan")
        .order_by("-current_period_end")
    )
    for sub in subs:
        if subscription_grants_access(sub, now):
            return sub
    return None


def plan_for(user):
    sub = active_subscription(user)
    if sub:
        return sub.plan
    return default_free_plan()


@dataclass
class Period:
    start: datetime
    end: datetime


def _calendar_month(now):
    start = datetime(now.year, now.month, 1, tzinfo=UTC)
    days = calendar.monthrange(now.year, now.month)[1]
    return Period(start, start + timedelta(days=days))


def _add_months(dt, months):
    month_index = dt.month - 1 + months
    year, month = dt.year + month_index // 12, month_index % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def current_period(user, now=None):
    """The window that `generations_per_period` applies to.

    Monthly plans: the provider billing period. Annual plans: monthly windows anchored on the billing-period
    start (so annual subscribers get the same monthly allowance as monthly subscribers). Free: calendar month.
    """
    now = now or timezone.now()
    sub = active_subscription(user, now)
    if sub and sub.current_period_start and sub.current_period_end:
        if sub.plan.interval == sub.plan.Interval.YEAR:
            start = sub.current_period_start
            for i in range(13):
                window_start, window_end = _add_months(start, i), _add_months(start, i + 1)
                if window_start <= now < window_end:
                    return Period(window_start, min(window_end, sub.current_period_end))
        return Period(sub.current_period_start, sub.current_period_end)
    return _calendar_month(now)


def _get_counter_for_update(user):
    try:
        UsageCounter.objects.get_or_create(user=user, defaults={"period_start": timezone.now(), "period_end": timezone.now()})
    except IntegrityError:
        pass  # created concurrently — fine
    return UsageCounter.objects.select_for_update().get(user=user)


def _roll(counter, period):
    if counter.period_start != period.start:
        counter.period_start = period.start
        counter.period_end = period.end
        counter.generations_used = 0


@dataclass
class QuotaStatus:
    plan: Plan
    limit: int
    used: int
    period: Period

    @property
    def remaining(self):
        return max(self.limit - self.used, 0)


def quota_status(user):
    plan = plan_for(user)
    period = current_period(user)
    counter = UsageCounter.objects.filter(user=user).first()
    used = counter.generations_used if counter and counter.period_start == period.start else 0
    return QuotaStatus(plan=plan, limit=plan.generations_per_period, used=used, period=period)


def reserve_generation(user, generation=None):
    """Atomically consume one unit of quota. Raises QuotaExceeded. Call inside or outside a transaction."""
    plan = plan_for(user)
    period = current_period(user)
    with transaction.atomic():
        counter = _get_counter_for_update(user)
        _roll(counter, period)
        if counter.generations_used >= plan.generations_per_period:
            counter.save()
            raise QuotaExceeded(f"You have used all {plan.generations_per_period} AI generations for this period.")
        counter.generations_used += 1
        counter.save()
        UsageLedgerEntry.objects.create(
            user=user,
            generation=generation,
            kind=UsageLedgerEntry.Kind.CONSUME,
            amount=1,
            period_start=period.start,
            reason=f"plan={plan.code}",
        )
        if generation is not None:
            type(generation).objects.filter(pk=generation.pk).update(quota_consumed=True, plan_code=plan.code)
            generation.quota_consumed = True
            generation.plan_code = plan.code
    return counter


def refund_generation(generation, reason):
    """Refund the unit consumed by a failed generation exactly once."""
    from cvs.models import GenerationRequest

    with transaction.atomic():
        gen = GenerationRequest.objects.select_for_update().get(pk=generation.pk)
        if not gen.quota_consumed or gen.quota_refunded:
            return False
        consume = UsageLedgerEntry.objects.filter(generation=gen, kind=UsageLedgerEntry.Kind.CONSUME).order_by("-created_at").first()
        counter = _get_counter_for_update(gen.user)
        if consume and counter.period_start == consume.period_start and counter.generations_used > 0:
            counter.generations_used -= 1
            counter.save()
        UsageLedgerEntry.objects.create(
            user=gen.user,
            generation=gen,
            kind=UsageLedgerEntry.Kind.REFUND,
            amount=-1,
            period_start=consume.period_start if consume else counter.period_start,
            reason=reason[:200],
        )
        gen.quota_refunded = True
        gen.save(update_fields=["quota_refunded"])
        generation.quota_refunded = True
    return True


# --- Feature gates -------------------------------------------------------------


def require_feature(user, feature):
    plan = plan_for(user)
    if not getattr(plan, feature, False):
        raise FeatureNotAvailable(feature)
    return plan


def limit_reached(user, limit_attr, current_count):
    plan = plan_for(user)
    return current_count >= getattr(plan, limit_attr)
