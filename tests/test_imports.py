import io
from datetime import timedelta
from unittest import mock

import pytest
from django.utils import timezone

from jobs.importers import expire_jobs, html_to_text, infer_location, run_source
from jobs.models import ImportRun, Job, JobSource

pytestmark = pytest.mark.django_db

CSV = (
    "title,description,application_url,external_id,eligibility_scope,eligible_countries,skills,compensation_min,compensation_max,compensation_currency,compensation_period,compensation_verified\n"
    'LLM Evaluator,Evaluate responses from large language models for quality.,https://p.example.com/a,A1,restricted,"US, PK","RLHF, English",20,30,USD,hour,true\n'
    "Bad row,short,not-a-url,A2,,,,,,,,\n"
    "LLM Evaluator,Evaluate responses from large language models for quality.,https://p.example.com/a,,,,,,,,,\n"
)


@pytest.fixture
def source(platform):
    return JobSource.objects.create(name="CSV", platform=platform, kind="csv")


def test_csv_import_validates_dedupes_and_logs(source):
    run = run_source(source, csv_file=io.BytesIO(CSV.encode()))
    assert run.status == ImportRun.Status.PARTIAL
    assert (run.created_count, run.failed_count, run.duplicate_count) == (1, 1, 1)
    job = Job.objects.get()
    assert job.status == Job.Status.DRAFT and job.needs_review  # imports need review by default
    assert job.eligible_countries == ["PK", "US"] and job.compensation_verified
    assert sorted(job.skills.values_list("name", flat=True)) == ["English", "RLHF"]
    assert run.errors[0]["row"] == 2


def test_csv_missing_columns(source):
    run = run_source(source, csv_file=io.BytesIO(b"title,foo\nx,y\n"))
    assert run.status == ImportRun.Status.FAILED and "missing required columns" in run.errors[0]["fatal"]


def test_restricted_without_countries_becomes_unknown(source):
    csv = "title,description,application_url,eligibility_scope\nX job,Long enough description for the listing here.,https://p.example.com/x,restricted\n"
    run_source(source, csv_file=io.BytesIO(csv.encode()))
    assert Job.objects.get().eligibility_scope == "unknown"


GREENHOUSE = {
    "jobs": [
        {
            "id": 101,
            "title": "AI Tutor - Physics",
            "absolute_url": "https://boards.example.com/101",
            "location": {"name": "Remote - United States"},
            "content": "&lt;p&gt;Teach &lt;b&gt;physics&lt;/b&gt; to AI models.&lt;/p&gt;&lt;script&gt;alert(1)&lt;/script&gt;&lt;ul&gt;&lt;li&gt;PhD preferred&lt;/li&gt;&lt;/ul&gt;",
            "updated_at": "2026-10-01T10:00:00Z",
        },
        {
            "id": 102,
            "title": "AI Trainer - Worldwide",
            "absolute_url": "https://boards.example.com/102",
            "location": {"name": "Remote (Anywhere)"},
            "content": "&lt;p&gt;Write and rank responses for models across many domains.&lt;/p&gt;",
            "updated_at": "2026-10-01T10:00:00Z",
        },
    ]
}


def _fake_get(payload):
    resp = mock.Mock(status_code=200)
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return mock.patch("jobs.importers.requests.get", return_value=resp)


def test_automated_source_requires_permission_attestation(platform):
    src = JobSource.objects.create(name="GH", platform=platform, kind="greenhouse", config={"board_token": "acme"})
    with _fake_get(GREENHOUSE) as get:
        run = run_source(src)
    assert run.status == ImportRun.Status.FAILED and not get.called


def test_greenhouse_import_refresh_and_expiry(platform):
    src = JobSource.objects.create(
        name="GH",
        platform=platform,
        kind="greenhouse",
        config={"board_token": "acme", "publish_immediately": True},
        permission_confirmed=True,
        expire_after_missed_runs=2,
    )
    with _fake_get(GREENHOUSE) as get:
        run = run_source(src)
    assert "boards-api.greenhouse.io/v1/boards/acme/jobs" in get.call_args[0][0]
    assert run.created_count == 2
    physics = Job.objects.get(external_id="101")
    assert physics.eligibility_scope == "restricted" and physics.eligible_countries == ["US"]
    assert "alert(1)" not in physics.description and "• PhD preferred" in physics.description
    assert Job.objects.get(external_id="102").eligibility_scope == "worldwide"

    # second run: same data → updates, not duplicates
    with _fake_get(GREENHOUSE):
        run2 = run_source(src)
    assert run2.updated_count == 2 and Job.objects.count() == 2

    # job 102 disappears from the feed twice → expired
    only_101 = {"jobs": [GREENHOUSE["jobs"][0]]}
    with _fake_get(only_101):
        run_source(src)
    assert Job.objects.get(external_id="102").status == Job.Status.PUBLISHED
    with _fake_get(only_101):
        run3 = run_source(src)
    assert run3.expired_count == 1 and Job.objects.get(external_id="102").status == Job.Status.EXPIRED


def test_lever_import(platform):
    data = [
        {
            "id": "abc",
            "text": "Coding Expert",
            "hostedUrl": "https://jobs.lever.co/x/abc",
            "applyUrl": "https://jobs.lever.co/x/abc/apply",
            "categories": {"location": "India", "commitment": "Contract"},
            "workplaceType": "remote",
            "descriptionPlain": "Write code to train AI systems. Strong Python skills required.",
            "createdAt": 1760000000000,
            "salaryRange": {"min": 25, "max": 40, "currency": "USD", "interval": "per-hour-wage"},
        }
    ]
    src = JobSource.objects.create(name="Lever", platform=platform, kind="lever", config={"site": "x"}, permission_confirmed=True)
    with _fake_get(data):
        run = run_source(src)
    job = Job.objects.get()
    assert run.created_count == 1 and job.employment_type == "contract" and job.eligible_countries == ["IN"]
    assert job.work_arrangement == "remote" and job.compensation_hourly_usd == 40


def test_rate_limited_source_logged(platform):
    src = JobSource.objects.create(name="GH", platform=platform, kind="greenhouse", config={"board_token": "acme"}, permission_confirmed=True)
    resp = mock.Mock(status_code=429)
    with mock.patch("jobs.importers.requests.get", return_value=resp):
        run = run_source(src)
    assert run.status == ImportRun.Status.FAILED and "429" in run.errors[0]["fatal"]


def test_duplicate_across_sources(platform, make_job):
    make_job(title="LLM Evaluator", application_url="https://p.example.com/a")
    src = JobSource.objects.create(name="CSV", platform=platform, kind="csv")
    csv = "title,description,application_url\nLLM Evaluator,Evaluate responses from large language models.,https://p.example.com/a?utm=x\n"
    run = run_source(src, csv_file=io.BytesIO(csv.encode()))
    assert run.duplicate_count == 1 and Job.objects.count() == 1


def test_expire_jobs_by_date_and_deadline(make_job):
    a = make_job(title="past expiry", expires_at=timezone.now() - timedelta(minutes=1))
    b = make_job(title="past deadline", application_deadline=(timezone.now() - timedelta(days=1)).date(), application_url="https://p.example.com/b")
    c = make_job(title="fine", expires_at=timezone.now() + timedelta(days=1), application_url="https://p.example.com/c")
    assert expire_jobs() == 2
    for j in (a, b, c):
        j.refresh_from_db()
    assert (a.status, b.status, c.status) == ("expired", "expired", "published")


def test_link_checker_flags_404_only(make_job):
    from jobs.importers import check_link

    job = make_job()
    with mock.patch("jobs.importers.requests.head", return_value=mock.Mock(status_code=404)):
        assert check_link(job) is True
    job.refresh_from_db()
    assert job.link_broken and job.needs_review
    import requests

    job2 = make_job(application_url="https://p.example.com/z")
    with mock.patch("jobs.importers.requests.head", side_effect=requests.ConnectionError):
        assert check_link(job2) is None
    job2.refresh_from_db()
    assert not job2.link_broken


def test_infer_location_is_conservative():
    assert infer_location("Remote")[1] == "unknown"
    assert infer_location("Remote - Worldwide")[1] == "worldwide"
    assert infer_location("Remote, UK or Pakistan")[2] == ["GB", "PK"]


def test_html_to_text_strips_scripts():
    assert html_to_text("<p>Hi</p><script>x()</script><style>p{}</style>") == "Hi"
