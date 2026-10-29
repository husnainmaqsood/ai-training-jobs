import threading
from datetime import UTC, datetime, timedelta
from unittest import mock

import pytest
from django.db import connection
from django.utils import timezone

from billing import entitlements
from billing.models import UsageCounter

JD = "We need Python and SQL experience to evaluate AI-generated code and data pipelines. " * 3


@pytest.mark.django_db
def test_free_quota_enforced(user, master_cv):
    from cvs.services import start_generation

    for _ in range(2):
        start_generation(user=user, master=master_cv, target_title="T", job_description=JD)
    with pytest.raises(entitlements.QuotaExceeded):
        start_generation(user=user, master=master_cv, target_title="T", job_description=JD)
    from cvs.models import GenerationRequest

    assert GenerationRequest.objects.count() == 2  # rejected attempt left no row behind


@pytest.mark.django_db
def test_quota_cannot_be_bypassed_via_endpoint(client_user, user, master_cv):
    for _ in range(3):
        client_user.post("/cv/tailor/", {"master_cv": master_cv.pk, "target_title": "T", "job_description": JD})
    from cvs.models import GenerationRequest

    assert GenerationRequest.objects.filter(user=user).count() == 2


@pytest.mark.django_db
def test_premium_quota_uses_billing_period(user, master_cv, subscription):
    status = entitlements.quota_status(user)
    assert status.limit == 30 and status.period.start == subscription.current_period_start


@pytest.mark.django_db
def test_free_period_resets_monthly(user, plans):
    counter = UsageCounter.objects.create(
        user=user, period_start=datetime(2020, 1, 1, tzinfo=UTC), period_end=datetime(2020, 2, 1, tzinfo=UTC), generations_used=2
    )
    entitlements.reserve_generation(user)
    counter.refresh_from_db()
    assert counter.generations_used == 1 and counter.period_start.month == timezone.now().month


@pytest.mark.django_db
def test_refund_only_once(user, master_cv):
    from cvs.services import start_generation

    gen = start_generation(user=user, master=master_cv, target_title="T", job_description=JD)
    assert entitlements.refund_generation(gen, "test") is True
    assert entitlements.refund_generation(gen, "test") is False
    assert entitlements.quota_status(user).used == 0


@pytest.mark.django_db(transaction=True)
def test_concurrent_reservations_cannot_exceed_quota(user, plans):
    """Ten threads race to reserve generations; exactly the plan limit (2) must succeed."""
    results = []
    barrier = threading.Barrier(10)

    def worker():
        try:
            barrier.wait()
            entitlements.reserve_generation(user)
            results.append("ok")
        except entitlements.QuotaExceeded:
            results.append("denied")
        finally:
            connection.close()

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count("ok") == 2 and results.count("denied") == 8
    assert UsageCounter.objects.get(user=user).generations_used == 2


@pytest.mark.django_db
def test_concurrent_generation_in_progress_blocked(user, master_cv):
    from cvs.models import GenerationRequest
    from cvs.services import GenerationRejected, start_generation

    GenerationRequest.objects.create(user=user, master_cv=master_cv, target_title="busy", job_description=JD, status="processing")
    with pytest.raises(GenerationRejected, match="already in progress"):
        start_generation(user=user, master=master_cv, target_title="T", job_description=JD)


@pytest.mark.django_db
def test_global_daily_limit_blocks_without_consuming_quota(user, master_cv, settings):
    from cvs.services import GenerationRejected, start_generation

    settings.AI_GLOBAL_DAILY_GENERATION_LIMIT = 0
    with pytest.raises(GenerationRejected):
        start_generation(user=user, master=master_cv, target_title="T", job_description=JD)
    assert entitlements.quota_status(user).used == 0


@pytest.mark.django_db
def test_hourly_user_limit(user, master_cv, settings, subscription):
    from cvs.services import GenerationRejected, start_generation

    settings.AI_USER_HOURLY_REQUEST_LIMIT = 1
    start_generation(user=user, master=master_cv, target_title="T", job_description=JD)
    with pytest.raises(GenerationRejected, match="last hour"):
        start_generation(user=user, master=master_cv, target_title="T", job_description=JD)


@pytest.mark.django_db
def test_stuck_generations_recovered_and_refunded(user, master_cv):
    from cvs.models import GenerationRequest
    from cvs.tasks import recover_stuck_generations

    gen = GenerationRequest.objects.create(user=user, master_cv=master_cv, target_title="x", job_description=JD)
    entitlements.reserve_generation(user, gen)
    GenerationRequest.objects.filter(pk=gen.pk).update(status="processing", created_at=timezone.now() - timedelta(hours=1))
    assert recover_stuck_generations() == 1
    gen.refresh_from_db()
    assert gen.status == "failed" and gen.quota_refunded and entitlements.quota_status(user).used == 0


@pytest.mark.django_db
def test_duplicate_task_delivery_is_idempotent(user, master_cv):
    from cvs.services import run_generation, start_generation

    gen = start_generation(user=user, master=master_cv, target_title="T", job_description=JD)
    with mock.patch("cvs.services.run_structured") as rs:
        run_generation(gen.pk)
    rs.assert_not_called()
    assert gen.versions.count() == 1


@pytest.mark.django_db
def test_annual_plan_gets_monthly_allowance_windows(user, plans):
    from billing.models import Subscription

    start = datetime(2026, 1, 31, 12, tzinfo=UTC)
    Subscription.objects.create(
        user=user,
        plan=plans["premium_annual"],
        provider_subscription_id="sub_annual",
        status="active",
        current_period_start=start,
        current_period_end=datetime(2027, 1, 31, 12, tzinfo=UTC),
    )
    p = entitlements.current_period(user, now=datetime(2026, 3, 5, tzinfo=UTC))
    assert p.start == datetime(2026, 2, 28, 12, tzinfo=UTC) and p.end == datetime(2026, 3, 31, 12, tzinfo=UTC)
    assert entitlements.plan_for(user).generations_per_period == 30
