import json
from datetime import timedelta
from pathlib import Path

import pytest
from django.utils import timezone

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _test_settings(settings, tmp_path):
    settings.AI_PROVIDER = "mock"
    settings.OPENAI_API_KEY = ""
    settings.STRIPE_SECRET_KEY = "sk_test_dummy"
    settings.STRIPE_WEBHOOK_SECRET = "whsec_test_secret"
    settings.PRIVATE_MEDIA_ROOT = tmp_path / "private"
    settings.MEDIA_ROOT = tmp_path / "private"
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    settings.ALLOWED_HOSTS = ["testserver", "localhost"]
    settings.AI_USER_HOURLY_REQUEST_LIMIT = 100
    settings.USER_ACCOUNTS_ENABLED = True  # the account/CV code stays tested; directory-only tests switch it off
    from django.core.cache import cache

    cache.clear()


@pytest.fixture
def plans(db):
    from django.core.management import call_command

    call_command("seed_initial", verbosity=0)
    from billing.models import Plan

    Plan.objects.filter(code="premium_monthly").update(provider_price_id="price_monthly_test")
    Plan.objects.filter(code="premium_annual").update(provider_price_id="price_annual_test")
    return {p.code: p for p in Plan.objects.all()}


@pytest.fixture
def user(db, django_user_model):
    u = django_user_model.objects.create_user(email="alice@example.com", password="Sup3r-secret-pass")
    u.email_verified = True
    u.save()
    u.profile.ai_processing_consent_at = timezone.now()
    u.profile.save()
    return u


@pytest.fixture
def other_user(db, django_user_model):
    u = django_user_model.objects.create_user(email="bob@example.com", password="Sup3r-secret-pass")
    u.email_verified = True
    u.save()
    return u


@pytest.fixture
def staff_user(db, django_user_model):
    return django_user_model.objects.create_superuser(email="admin@example.com", password="Sup3r-secret-pass")


@pytest.fixture
def client_user(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def platform(db):
    from jobs.models import Platform

    return Platform.objects.create(name="TestPlatform", slug="testplatform", website="https://platform.example.com")


@pytest.fixture
def make_job(db, platform):
    from jobs.models import Job

    def _make(**kw):
        defaults = dict(
            title="Python AI Trainer",
            platform=platform,
            description="Evaluate Python code written by AI models. " * 3,
            application_url="https://platform.example.com/apply/1",
            status=Job.Status.PUBLISHED,
            eligibility_scope=Job.Eligibility.WORLDWIDE,
            published_at=timezone.now(),
        )
        defaults.update(kw)
        return Job.objects.create(**defaults)

    return _make


@pytest.fixture
def sample_cv_text():
    return (FIXTURES / "sample_cv.txt").read_text()


@pytest.fixture
def master_cv(user, sample_cv_text, plans):
    from cvs.services import create_master_cv_from_text

    return create_master_cv_from_text(user, "Test CV", sample_cv_text)


@pytest.fixture
def subscription(user, plans):
    from billing.models import Subscription

    now = timezone.now()
    return Subscription.objects.create(
        user=user,
        plan=plans["premium_monthly"],
        provider_subscription_id="sub_test_1",
        status="active",
        current_period_start=now - timedelta(days=3),
        current_period_end=now + timedelta(days=27),
    )


def stripe_signed(payload: dict, secret="whsec_test_secret"):
    import time

    import stripe

    body = json.dumps(payload)
    ts = int(time.time())
    sig = stripe.WebhookSignature._compute_signature(f"{ts}.{body}", secret)
    return body, f"t={ts},v1={sig}"
