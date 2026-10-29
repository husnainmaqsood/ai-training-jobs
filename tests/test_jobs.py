from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from jobs.models import Job

pytestmark = pytest.mark.django_db


def titles(resp):
    return [j.title for j in resp.context["jobs"]]


def test_public_list_hides_drafts_expired_broken_and_demo(client, make_job):
    make_job(title="Visible")
    make_job(title="Draft", status=Job.Status.DRAFT)
    make_job(title="Expired", expires_at=timezone.now() - timedelta(hours=1))
    make_job(title="Broken", link_broken=True)
    make_job(title="Demo", is_demo=True)
    assert titles(client.get(reverse("jobs:list"))) == ["Visible"]


def test_full_text_search(client, make_job):
    make_job(title="Medical Doctor AI Reviewer", description="Review clinical answers produced by LLMs. " * 3)
    make_job(title="Python Coding Expert")
    assert titles(client.get(reverse("jobs:list"), {"q": "clinical"})) == ["Medical Doctor AI Reviewer"]
    assert titles(client.get(reverse("jobs:list"), {"q": "python"})) == ["Python Coding Expert"]


def test_search_includes_skills(client, make_job):
    from jobs.models import Skill

    j = make_job(title="Generic evaluator")
    j.skills.add(Skill.objects.create(name="Kotlin"))
    j.refresh_search_vector()
    assert titles(client.get(reverse("jobs:list"), {"q": "kotlin"})) == ["Generic evaluator"]


def test_country_filter_distinguishes_worldwide_restricted_and_unknown(client, make_job):
    make_job(title="Worldwide", eligibility_scope="worldwide")
    make_job(title="Worldwide excl PK", eligibility_scope="worldwide", excluded_countries=["PK"])
    make_job(title="US only", eligibility_scope="restricted", eligible_countries=["US"])
    make_job(title="PK and IN", eligibility_scope="restricted", eligible_countries=["PK", "IN"])
    make_job(title="Unknown", eligibility_scope="unknown")
    assert sorted(titles(client.get(reverse("jobs:list"), {"country": "PK"}))) == ["PK and IN", "Worldwide"]
    assert sorted(titles(client.get(reverse("jobs:list"), {"country": "US"}))) == ["US only", "Worldwide", "Worldwide excl PK"]
    assert sorted(titles(client.get(reverse("jobs:list"), {"worldwide_only": "on"}))) == ["Worldwide", "Worldwide excl PK"]


def test_remote_is_not_labelled_worldwide():
    j = Job(title="x", eligibility_scope="unknown", work_arrangement="remote")
    assert "not verified" in j.eligibility_label
    j2 = Job(title="x", eligibility_scope="restricted", eligible_countries=["US"], work_arrangement="remote")
    assert j2.eligibility_label == "Remote · United States only"


def test_filters_by_type_platform_category_and_pay(client, make_job, platform):
    from jobs.models import Category, Platform

    cat = Category.objects.create(name="Coding", slug="coding")
    other = Platform.objects.create(name="Other", slug="other", website="https://o.example.com")
    make_job(
        title="A",
        employment_type="hourly",
        category=cat,
        compensation_min=40,
        compensation_max=60,
        compensation_currency="USD",
        compensation_period="hour",
        compensation_verified=True,
    )
    make_job(
        title="B",
        employment_type="full_time",
        platform=other,
        compensation_min=10,
        compensation_currency="USD",
        compensation_period="hour",
        compensation_verified=True,
    )
    make_job(title="C unverified pay", compensation_min=100, compensation_currency="USD", compensation_period="hour")
    assert titles(client.get(reverse("jobs:list"), {"employment_type": "hourly"})) == ["A"]
    assert titles(client.get(reverse("jobs:list"), {"platform": "other"})) == ["B"]
    assert titles(client.get(reverse("jobs:list"), {"category": "coding"})) == ["A"]
    assert titles(client.get(reverse("jobs:list"), {"min_hourly": "30"})) == ["A"]
    assert titles(client.get(reverse("jobs:list"), {"sort": "pay"}))[:2] == ["A", "B"]


def test_unverified_compensation_not_displayed(client, make_job):
    job = make_job(title="Secret pay", compensation_min=99, compensation_max=120, compensation_currency="USD", compensation_period="hour")
    resp = client.get(job.get_absolute_url())
    assert b"$99" not in resp.content and b"not verified" in resp.content


def test_newest_first_and_pagination(client, make_job):
    now = timezone.now()
    for i in range(25):
        make_job(title=f"Job {i:02d}", published_at=now - timedelta(hours=i))
    first = client.get(reverse("jobs:list"))
    assert titles(first)[0] == "Job 00" and len(titles(first)) == 20
    second = client.get(reverse("jobs:list"), {"page": 2})
    assert len(titles(second)) == 5
    assert b'rel="prev"' in second.content


def test_htmx_returns_partial(client, make_job):
    make_job()
    resp = client.get(reverse("jobs:list"), HTTP_HX_REQUEST="true")
    assert b"<html" not in resp.content and b"listing" in resp.content


def test_detail_redirects_to_canonical_slug_and_expired_gives_410(client, make_job):
    job = make_job(title="Canonical Job")
    resp = client.get(reverse("jobs:detail", args=[job.pk, "wrong-slug"]))
    assert resp.status_code == 301 and resp["Location"] == job.get_absolute_url()
    job.status = Job.Status.EXPIRED
    job.save()
    assert client.get(job.get_absolute_url()).status_code == 410


def test_draft_job_is_404_for_public(client, make_job):
    job = make_job(status=Job.Status.DRAFT)
    assert client.get(job.get_absolute_url()).status_code == 404


def test_save_job_respects_plan_limit(client_user, user, make_job, plans):
    plans["free"].max_saved_jobs = 1
    plans["free"].save()
    j1, j2 = make_job(title="One"), make_job(title="Two", application_url="https://platform.example.com/apply/2")
    client_user.post(reverse("jobs:toggle_save", args=[j1.pk]))
    client_user.post(reverse("jobs:toggle_save", args=[j2.pk]))
    from jobs.models import SavedJob

    assert SavedJob.objects.filter(user=user).count() == 1
    client_user.post(reverse("jobs:toggle_save", args=[j1.pk]))  # toggle off
    assert SavedJob.objects.filter(user=user).count() == 0


def test_report_flags_for_review_after_multiple_reports(client, make_job):
    job = make_job()
    for _ in range(3):
        client.post(reverse("jobs:report", args=[job.pk]), {"reason": "scam"})
    job.refresh_from_db()
    assert job.needs_review


def test_seo_endpoints(client, make_job):
    job = make_job(eligibility_scope="restricted", eligible_countries=["US"])
    assert b"Sitemap:" in client.get("/robots.txt").content
    sm = client.get("/sitemap.xml").content
    assert job.get_absolute_url().encode() in sm
    detail = client.get(job.get_absolute_url()).content
    assert b"application/ld+json" in detail and b'"applicantLocationRequirements"' in detail
    assert b'rel="canonical"' in detail


def test_jsonld_omitted_when_eligibility_cannot_be_expressed(client, make_job):
    job = make_job(eligibility_scope="unknown")
    assert b"application/ld+json" not in client.get(job.get_absolute_url()).content


def test_thin_landing_pages_are_noindex(client, make_job):
    from jobs.models import Category

    cat = Category.objects.create(name="Tiny", slug="tiny")
    make_job(category=cat)
    assert b'content="noindex' in client.get(reverse("jobs:category", args=["tiny"])).content
