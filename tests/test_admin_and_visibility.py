import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.urls import reverse

from accounts.models import User
from jobs.categories import infer_category_slug
from jobs.models import Category, Job
from jobs.stats import directory_stats

pytestmark = pytest.mark.django_db


@pytest.fixture
def categories(db):
    call_command("seed_initial", verbosity=0)
    return {c.slug: c for c in Category.objects.all()}


# --- Single admin -----------------------------------------------------------------------------


def test_only_one_admin_can_exist(staff_user, user):
    with pytest.raises(ValueError, match="already has an admin"):
        User.objects.create_superuser(email="second@example.com", password="Sup3r-secret-pass")
    user.is_staff = True
    with pytest.raises(IntegrityError), transaction.atomic():
        user.save()


def test_superuser_must_be_the_admin(user):
    user.is_superuser = True
    with pytest.raises(IntegrityError), transaction.atomic():
        user.save()


def test_set_admin_hands_the_role_over(staff_user, user):
    call_command("set_admin", user.email, stdout=None)
    staff_user.refresh_from_db()
    user.refresh_from_db()
    assert (user.is_staff, user.is_superuser) == (True, True)
    assert (staff_user.is_staff, staff_user.is_superuser) == (False, False)
    assert list(User.objects.filter(is_staff=True)) == [user]


def test_set_admin_unknown_email(staff_user):
    with pytest.raises(CommandError):
        call_command("set_admin", "nobody@example.com")


# --- New listings show up everywhere -----------------------------------------------------------


def test_job_added_in_django_admin_is_listed_everywhere(client, staff_user, platform, categories):
    client.force_login(staff_user)
    resp = client.post(
        reverse("admin:jobs_job_add"),
        {
            "title": "Senior Python Code Reviewer",
            "slug": "",
            "platform": platform.pk,
            "status": "published",
            "employment_type": "contract",
            "work_arrangement": "remote",
            "experience_level": "any",
            "eligibility_scope": "worldwide",
            "application_url": "https://platform.example.com/jobs/42",
            "compensation_min": "50",
            "compensation_max": "50",
            "compensation_currency": "USD",
            "compensation_period": "hour",
            "compensation_verified": "on",
        },
    )
    assert resp.status_code == 302, resp.content.decode()[:3000]
    job = Job.objects.get(title="Senior Python Code Reviewer")
    assert job.published_at is not None and job.compensation_hourly_usd == 50

    stats = directory_stats()
    assert stats["total"] == 1 and stats["by_company"][0]["n"] == 1
    client.logout()
    for url in ["/", reverse("jobs:list"), reverse("jobs:platform_detail", args=[platform.slug])]:
        assert b"Senior Python Code Reviewer" in client.get(url).content, url


def test_reopened_job_gets_published_at_and_is_listed(client, staff_user, make_job):
    job = make_job(status=Job.Status.CLOSED, published_at=None)
    client.force_login(staff_user)
    client.post(reverse("admin:jobs_job_changelist"), {"action": "publish", "_selected_action": [job.pk]})
    job.refresh_from_db()
    assert job.status == Job.Status.PUBLISHED and job.published_at is not None
    assert directory_stats()["total"] == 1


def test_uncategorised_jobs_are_counted_as_other(make_job):
    make_job(title="Generalist")  # no category, nothing to infer from (make_job bypasses the forms)
    rows = directory_stats()["by_category"]
    assert rows[-1]["category__name"] == "Other roles" and rows[-1]["n"] == 1


def test_demo_jobs_hidden_unless_enabled(settings, make_job):
    settings.DEBUG = True
    make_job(is_demo=True)
    assert Job.objects.public().count() == 0
    settings.SHOW_DEMO_JOBS = True
    assert Job.objects.public().count() == 1


@pytest.mark.parametrize(
    "title,slug",
    [
        ("AI Math Problem Reviewer", "domain-expert-ai-training"),
        ("Full-Stack Software Engineer - AI Evaluation", "ai-coding-software-evaluation"),
        ("AI Search Relevance Evaluator", "search-ads-evaluation"),
        ("Machine Learning Data Annotator", "data-annotation-labelling"),
        ("AI Prompt and Response Evaluator", "llm-evaluation-rlhf"),
        ("Gardener", ""),
    ],
)
def test_infer_category(title, slug):
    assert infer_category_slug(title) == slug


# --- Clicking a listing goes to the hiring platform ------------------------------------------


def test_listing_row_links_to_the_job_link(client, make_job):
    job = make_job(application_url="https://platform.example.com/apply/99")
    page = client.get(reverse("jobs:list")).content.decode()
    assert f'href="{job.get_apply_url()}?from=job_list"' in page
    resp = client.get(job.get_apply_url())
    assert resp.status_code == 302 and resp["Location"] == "https://platform.example.com/apply/99"


# --- Directory-only release: no public accounts -----------------------------------------------


def test_anonymous_visitors_can_browse_every_public_page(client, make_job, categories, settings):
    settings.USER_ACCOUNTS_ENABLED = False
    job = make_job(category=categories["llm-evaluation-rlhf"])
    pages = [
        "/",
        reverse("jobs:list"),
        reverse("jobs:list") + "?q=python&sort=pay",
        reverse("jobs:category", args=["llm-evaluation-rlhf"]),
        reverse("jobs:platform_list"),
        reverse("jobs:platform_detail", args=[job.platform.slug]),
        reverse("jobs:country", args=["PK"]),
        job.get_absolute_url(),
        reverse("core:faq"),
        "/sitemap.xml",
        "/robots.txt",
    ]
    for url in pages:
        resp = client.get(url)
        assert resp.status_code == 200, url
        assert b"Sign up" not in resp.content and b"CV Builder" not in resp.content, url
    assert client.get(job.get_apply_url())["Location"] == job.application_url


def test_account_and_cv_urls_are_off_for_this_release(client, settings, make_job):
    settings.USER_ACCOUNTS_ENABLED = False
    job = make_job()
    for url in [
        "/accounts/login/",
        "/accounts/register/",
        "/accounts/profile/",
        "/cv/builder/",
        "/pricing/",
        "/billing/",
        "/applications/",
        "/jobs/saved/",
        "/how-it-works/",
    ]:
        assert client.get(url).status_code == 404, url
    assert client.post(f"/jobs/{job.pk}/save/").status_code == 404
    assert client.get("/admin/login/").status_code == 200  # the admin login is unaffected
