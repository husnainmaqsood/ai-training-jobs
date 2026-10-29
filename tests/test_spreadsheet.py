"""Excel/CSV import through Django Admin, and its effect on the public site."""

import io
import re
from datetime import timedelta
from decimal import Decimal
from unittest import mock

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from jobs import spreadsheet
from jobs.models import ImportRun, Job, Platform, canonical_url
from jobs.stats import directory_stats
from referrals.models import ClickEvent, ReferralProgram

pytestmark = pytest.mark.django_db

HEADERS = ["Title", "Company", "Job Link", "Hourly Rate", "Category", "Eligible Countries", "Status", "Posted Date"]
ROWS = [
    [
        "Python Code Reviewer",
        "Mercor",
        "https://mercor.example/jobs/1",
        "$50/hr",
        "AI coding & software evaluation",
        "Worldwide",
        "open",
        "2026-10-01",
    ],
    ["Medical Doctor AI Trainer", "Micro1", "https://micro1.example/jobs/7", "90", "", "US, United Kingdom", "open", "2026-10-02"],
    ["Urdu Writing Evaluator", "micro 1", "https://www.micro1.example/jobs/8/?utm_source=x", "", "", "", "", ""],
]


def xlsx(rows, headers=HEADERS, name="jobs.xlsx"):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return SimpleUploadedFile(name, buf.getvalue())


def csv_file(text, name="jobs.csv"):
    return SimpleUploadedFile(name, text.encode())


@pytest.fixture
def categories(db):
    call_command("seed_initial", verbosity=0)


@pytest.fixture
def admin_client(client, staff_user):
    client.force_login(staff_user)
    return client


def upload(admin_client, file, mode="upsert"):
    """Upload through the admin and return the pending ImportRun."""
    resp = admin_client.post(reverse("admin:jobs_job_import"), {"file": file, "mode": mode})
    assert resp.status_code == 302, resp.content.decode()[:2000]
    return ImportRun.objects.latest("pk")


def confirm(admin_client, run, **extra):
    return admin_client.post(reverse("admin:jobs_job_import_preview", args=[run.pk]), {"action": "Import", **extra})


def import_file(admin_client, file, mode="upsert", **extra):
    run = upload(admin_client, file, mode)
    confirm(admin_client, run, **extra)
    run.refresh_from_db()
    return run


# --- Parsing --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("$40/hr", (Decimal(40), Decimal(40), "USD", "hour")),
        ("$40–60 per hour", (Decimal(40), Decimal(60), "USD", "hour")),
        ("Up to $100/hr", (None, Decimal(100), "USD", "hour")),
        ("£2,500/month", (Decimal(2500), Decimal(2500), "GBP", "month")),
        ("80k USD per year", (Decimal(80000), Decimal(80000), "USD", "year")),
        ("Competitive", None),
        ("$40", None),  # no period → not a structured rate on its own
    ],
)
def test_parse_rate(text, expected):
    assert spreadsheet.parse_rate(text) == expected


def test_reads_xlsx_and_csv_with_flexible_headers():
    rows = spreadsheet.read_rows(xlsx(ROWS[:1]))
    assert rows[0]["title"] == "Python Code Reviewer" and rows[0]["platform"] == "Mercor" and rows[0]["_row"] == 2
    assert rows[0]["hourly_rate"] == "$50/hr" and rows[0]["eligible_countries"] == "Worldwide"
    rows = spreadsheet.read_rows(csv_file("job title;employer;url;min pay;max pay;period\nWriter;Turing;https://t.example/1;20;30;week\n"))
    assert (rows[0]["platform"], rows[0]["job_link"], rows[0]["minimum_rate"], rows[0]["rate_period"]) == (
        "Turing",
        "https://t.example/1",
        "20",
        "week",
    )


@pytest.mark.parametrize(
    "file,message",
    [
        (csv_file("title,company\nx,y\n"), "Missing required column(s): job_link"),
        (SimpleUploadedFile("old.xls", b"\xd0\xcf\x11\xe0junk"), ".xls files aren't supported"),
        (SimpleUploadedFile("broken.xlsx", b"PK\x03\x04notreally"), "valid .xlsx"),
        (csv_file("title,platform,job_link\n"), "No job rows"),
        (SimpleUploadedFile("x.pdf", b"%PDF"), "Excel (.xlsx) or CSV"),
    ],
)
def test_rejects_bad_files(file, message):
    with pytest.raises(spreadsheet.SpreadsheetError, match=re.escape(message)):
        spreadsheet.read_rows(file)


def test_rejects_files_over_the_row_limit(settings):
    with mock.patch.object(spreadsheet, "MAX_ROWS", 2):
        body = "title,platform,job_link\n" + "".join(f"T{i},P,https://p.example/{i}\n" for i in range(3))
        with pytest.raises(spreadsheet.SpreadsheetError, match="more than 2 job rows"):
            spreadsheet.read_rows(csv_file(body))


def test_canonical_url_ignores_cosmetic_differences():
    assert canonical_url("https://www.Micro1.example/jobs/8/?utm_source=x&b=2&a=1#apply") == "micro1.example/jobs/8?a=1&b=2"
    assert canonical_url("http://micro1.example/jobs/8") == canonical_url("https://micro1.example/jobs/8/")


def test_row_validation_reports_useful_errors(categories):
    rows = spreadsheet.read_rows(
        csv_file(
            "title,platform,job_link,minimum_rate,maximum_rate,currency,rate_period,eligible_countries,posted_date,expiry_date,status,work_arrangement\n"
            "Ok row,Acme,https://acme.example/1,10,20,USD,hour,PK,2026-01-05,2099-01-01,open,remote\n"
            ",Acme,ftp://acme.example/2,30,20,US Dollars,fortnight,Narnia,yesterday,soon,maybe,space\n"
        )
    )
    results, _ = spreadsheet.analyse(rows)
    ok, bad = results
    assert ok.status == "new" and ok.values["compensation_min"] == Decimal(10) and ok.values["eligible_countries"] == ["PK"]
    text = " ".join(bad.errors)
    for expected in (
        "title is empty",
        "job_link",
        "minimum_rate 30 is higher",
        "Narnia",
        "posted_date 'yesterday'",
        "expiry_date 'soon'",
        "status 'maybe'",
        "work_arrangement 'space'",
    ):
        assert expected in text, expected
    assert bad.status == "error"


# --- Admin workflow -------------------------------------------------------------------------------


def test_upload_adds_jobs_with_normalised_fields(admin_client, categories):
    # seed_initial already has the platform as "micro1"; "Micro1" and "micro 1" in the sheet must both map to it.
    run = import_file(admin_client, xlsx(ROWS))
    assert (run.status, run.created_count, run.updated_count, run.duplicate_count, run.failed_count) == ("success", 3, 0, 0, 0)

    reviewer = Job.objects.get(title="Python Code Reviewer")
    assert reviewer.platform.name == "Mercor" and reviewer.status == Job.Status.PUBLISHED
    assert (reviewer.compensation_min, reviewer.compensation_currency, reviewer.compensation_period) == (Decimal(50), "USD", "hour")
    assert reviewer.compensation_hourly_usd == Decimal(50) and reviewer.category.slug == "ai-coding-software-evaluation"
    assert reviewer.eligibility_scope == Job.Eligibility.WORLDWIDE and timezone.localdate(reviewer.published_at).isoformat() == "2026-10-01"

    doctor = Job.objects.get(title="Medical Doctor AI Trainer")
    assert doctor.eligible_countries == ["GB", "US"] and doctor.compensation_max == Decimal(90)
    assert doctor.category.slug == "domain-expert-ai-training"  # inferred from the title
    # "micro 1" is the existing Micro1 platform, not a new one
    assert Platform.objects.filter(name__icontains="micro").count() == 1
    assert Job.objects.get(title="Urdu Writing Evaluator").platform == doctor.platform


def test_uploading_the_same_file_twice_creates_no_duplicates(admin_client, categories):
    import_file(admin_client, xlsx(ROWS))
    ids = sorted(Job.objects.values_list("pk", flat=True))
    run = import_file(admin_client, xlsx(ROWS))
    assert (run.created_count, run.updated_count, run.duplicate_count) == (0, 0, 3)
    assert sorted(Job.objects.values_list("pk", flat=True)) == ids


def test_updated_file_updates_existing_jobs_in_place(admin_client, categories):
    import_file(admin_client, xlsx(ROWS))
    doctor_id = Job.objects.get(title="Medical Doctor AI Trainer").pk
    changed = [row[:] for row in ROWS]
    changed[1][0], changed[1][2], changed[1][3] = "Physician AI Trainer", "http://micro1.example/jobs/7/?utm_campaign=y", "120"
    changed[2][6] = "closed"
    run = import_file(admin_client, xlsx(changed))
    assert (run.created_count, run.updated_count, run.duplicate_count) == (0, 2, 1)
    doctor = Job.objects.get(pk=doctor_id)  # same row, same ID, same public URL
    assert doctor.title == "Physician AI Trainer" and doctor.compensation_max == Decimal(120)
    assert Job.objects.get(title="Urdu Writing Evaluator").status == Job.Status.CLOSED
    assert Job.objects.count() == 3


def test_external_id_matches_even_when_the_link_changes(admin_client, categories):
    head = "external_id,title,platform,job_link\n"
    import_file(admin_client, csv_file(head + "A-1,Rater,Acme,https://acme.example/old\n"))
    job = Job.objects.get()
    import_file(admin_client, csv_file(head + "A-1,Rater,Acme,https://acme.example/new\n"))
    job.refresh_from_db()
    assert Job.objects.count() == 1 and job.application_url == "https://acme.example/new"


def test_empty_cells_do_not_erase_existing_data(admin_client, categories):
    import_file(admin_client, xlsx(ROWS[:1]))
    import_file(admin_client, csv_file("title,platform,job_link,hourly_rate,category\nPython Code Reviewer,Mercor,https://mercor.example/jobs/1,,\n"))
    job = Job.objects.get()
    assert job.compensation_min == Decimal(50) and job.category is not None


def test_repeated_rows_and_cross_platform_links_are_not_imported(admin_client, categories):
    import_file(admin_client, xlsx(ROWS[:1]))
    body = (
        "title,platform,job_link\n"
        "New one,Acme,https://acme.example/1\n"
        "New one again,Acme,https://acme.example/1/\n"
        "Stolen link,Acme,https://mercor.example/jobs/1\n"
    )
    run = import_file(admin_client, csv_file(body))
    assert (run.created_count, run.duplicate_count, run.failed_count, run.status) == (1, 1, 1, "partial")
    assert "already belongs to job" in run.errors[0]["errors"][0] and run.errors[0]["row"] == 4
    assert Job.objects.count() == 2


def test_invalid_rows_are_rejected_with_a_report_and_valid_rows_still_import(admin_client, categories):
    body = "title,platform,job_link,status\nGood,Acme,https://acme.example/1,open\nBad,Acme,not-a-link,open\n"
    run = upload(admin_client, csv_file(body))
    preview = admin_client.get(reverse("admin:jobs_job_import_preview", args=[run.pk])).content.decode()
    assert "Nothing has been saved yet" in preview and "must be a full http" in preview
    assert Job.objects.count() == 0  # preview writes nothing
    confirm(admin_client, run)
    run.refresh_from_db()
    assert (run.created_count, run.failed_count) == (1, 1)
    assert run.errors == [{"row": 3, "title": "Bad", "errors": ["job_link 'not-a-link' must be a full http:// or https:// URL."]}]
    page = admin_client.get(reverse("admin:jobs_importrun_change", args=[run.pk])).content.decode()
    assert "must be a full http" in page


def test_failed_import_is_rolled_back_completely(admin_client, categories):
    run = upload(admin_client, xlsx(ROWS))
    real_write = spreadsheet._write
    calls = []

    def flaky(r):
        calls.append(r)
        if len(calls) == 2:
            raise RuntimeError("disk on fire")
        return real_write(r)

    with mock.patch.object(spreadsheet, "_write", flaky):
        confirm(admin_client, run)
    run.refresh_from_db()
    assert run.status == ImportRun.Status.FAILED and "rolled back" in run.errors[0]["errors"][0]
    assert Job.objects.count() == 0  # the first row was written, then undone


def test_cancel_writes_nothing(admin_client):
    run = upload(admin_client, xlsx(ROWS))
    admin_client.post(reverse("admin:jobs_job_import_preview", args=[run.pk]), {"action": "cancel"})
    run.refresh_from_db()
    assert run.status == ImportRun.Status.CANCELLED and Job.objects.count() == 0


def test_a_run_cannot_be_applied_twice(admin_client, categories):
    run = import_file(admin_client, xlsx(ROWS))
    with pytest.raises(spreadsheet.ImportBlocked):
        spreadsheet.apply(run)
    assert Job.objects.count() == 3


def test_upsert_mode_never_closes_missing_jobs(admin_client, categories, make_job):
    keep = make_job(title="Listed by hand", application_url="https://platform.example.com/x")
    import_file(admin_client, xlsx(ROWS[:1]))
    keep.refresh_from_db()
    assert keep.status == Job.Status.PUBLISHED


def test_snapshot_mode_closes_missing_jobs_only_after_confirmation(admin_client, categories, make_job):
    missing = make_job(title="Gone from the sheet", application_url="https://platform.example.com/gone")
    run = upload(admin_client, xlsx(ROWS[:1]), mode="snapshot")
    preview = admin_client.get(reverse("admin:jobs_job_import_preview", args=[run.pk])).content.decode()
    assert "Gone from the sheet" in preview and "will be closed" in preview

    confirm(admin_client, run)  # no confirmation tick → blocked, nothing written
    run.refresh_from_db()
    missing.refresh_from_db()
    assert run.status == ImportRun.Status.PENDING and missing.status == Job.Status.PUBLISHED and Job.objects.count() == 1

    confirm(admin_client, run, close_confirmed="1", confirm_close="1")
    run.refresh_from_db()
    missing.refresh_from_db()
    assert run.status == ImportRun.Status.SUCCESS and run.expired_count == 1
    assert missing.status == Job.Status.CLOSED and Job.objects.get(title="Python Code Reviewer").is_public


def test_snapshot_mode_refuses_when_rows_are_rejected(admin_client, categories, make_job):
    make_job(title="Would be closed")
    run = upload(admin_client, csv_file("title,platform,job_link\nBad,Acme,nope\n"), mode="snapshot")
    confirm(admin_client, run, close_confirmed="1", confirm_close="1")
    run.refresh_from_db()
    assert run.status == ImportRun.Status.PENDING and Job.objects.get().status == Job.Status.PUBLISHED


def test_templates_are_valid():
    for content, name in [(spreadsheet.template_xlsx(), "t.xlsx"), (spreadsheet.template_csv(), "t.csv")]:
        rows = spreadsheet.read_rows(SimpleUploadedFile(name, content))
        assert set(spreadsheet.TEMPLATE_HEADERS) <= set(rows[0])
        results, _ = spreadsheet.analyse(rows)
        assert "example row" in results[0].errors[0]


def test_admin_template_downloads(admin_client):
    resp = admin_client.get(reverse("admin:jobs_job_import_template", args=["xlsx"]))
    assert resp.status_code == 200 and resp["Content-Disposition"].endswith('.xlsx"')
    assert b"job_link" in admin_client.get(reverse("admin:jobs_job_import_template", args=["csv"])).content


# --- Permissions ------------------------------------------------------------------------------------


IMPORT_URLS = [("admin:jobs_job_import", []), ("admin:jobs_job_import_template", ["csv"]), ("admin:jobs_job_changelist", [])]


def test_anonymous_and_regular_users_cannot_reach_admin_or_import(client, client_user):
    run = spreadsheet.start([{"title": "x", "platform": "y", "job_link": "https://y.example", "_row": 2}])
    urls = [reverse(n, args=a) for n, a in IMPORT_URLS] + [reverse("admin:jobs_job_import_preview", args=[run.pk])]
    for c in (client_user, client.__class__()):
        for url in urls:
            resp = c.get(url)
            assert resp.status_code == 302 and "/admin/login/" in resp["Location"], url
        resp = c.post(reverse("admin:jobs_job_import_preview", args=[run.pk]), {"action": "Import"})
        assert resp.status_code == 302 and "/admin/login/" in resp["Location"]
    run.refresh_from_db()
    assert run.status == ImportRun.Status.PENDING and Job.objects.count() == 0


def test_admin_can_manage_listings(admin_client, make_job):
    job = make_job()
    assert b"Import from Excel" in admin_client.get(reverse("admin:jobs_job_changelist")).content
    assert admin_client.get(reverse("admin:jobs_job_change", args=[job.pk])).status_code == 200
    admin_client.post(reverse("admin:jobs_job_changelist"), {"action": "close", "_selected_action": [job.pk]})
    job.refresh_from_db()
    assert job.status == Job.Status.CLOSED
    admin_client.post(reverse("admin:jobs_job_changelist"), {"action": "publish", "_selected_action": [job.pk]})
    job.refresh_from_db()
    assert job.status == Job.Status.PUBLISHED
    # Bulk delete goes through Django's confirmation page first.
    resp = admin_client.post(reverse("admin:jobs_job_changelist"), {"action": "delete_selected", "_selected_action": [job.pk]})
    assert resp.status_code == 200 and b"Are you sure" in resp.content and Job.objects.filter(pk=job.pk).exists()


def test_old_staff_urls_redirect_to_django_admin(client):
    for url in ["/staff/", "/staff/jobs/", "/staff/import/"]:
        resp = client.get(url)
        assert resp.status_code == 302 and resp["Location"] == "/admin/"


# --- The public site reflects imports ---------------------------------------------------------------


def test_import_updates_every_public_view(client, admin_client, categories, settings):
    settings.USER_ACCOUNTS_ENABLED = False
    import_file(admin_client, xlsx(ROWS))
    admin_client.logout()
    job = Job.objects.get(title="Python Code Reviewer")

    stats = directory_stats()
    assert stats["total"] == 3 and stats["companies"] == 2
    assert {r["platform__name"]: r["n"] for r in stats["by_company"]} == {"Mercor": 1, "micro1": 2}
    assert stats["recent"][0].title == "Urdu Writing Evaluator"  # no posted date → published now

    home = client.get("/").content.decode()
    assert "3 roles" in home and "Python Code Reviewer" in home and "micro1" in home
    listing = client.get(reverse("jobs:list"))
    assert listing.context["total"] == 3
    assert [j.title for j in client.get(reverse("jobs:list"), {"q": "python"}).context["jobs"]] == ["Python Code Reviewer"]
    assert [j.title for j in client.get(reverse("jobs:list"), {"platform": "micro1"}).context["jobs"]] == [
        "Urdu Writing Evaluator",
        "Medical Doctor AI Trainer",
    ]
    assert [j.title for j in client.get(reverse("jobs:list"), {"sort": "pay"}).context["jobs"]][:2] == [
        "Medical Doctor AI Trainer",
        "Python Code Reviewer",
    ]
    assert [j.title for j in client.get(reverse("jobs:list"), {"country": "GB"}).context["jobs"]] == [
        "Medical Doctor AI Trainer",
        "Python Code Reviewer",
    ]

    category = client.get(reverse("jobs:category", args=["ai-coding-software-evaluation"]))
    assert [j.title for j in category.context["jobs"]] == ["Python Code Reviewer"]
    platforms = client.get(reverse("jobs:platform_list"))
    assert {p.name: p.open_jobs for p in platforms.context["platforms"]}["micro1"] == 2
    assert client.get(reverse("jobs:platform_detail", args=["micro1"])).status_code == 200
    detail = client.get(job.get_absolute_url())
    assert detail.status_code == 200 and b"$50" in detail.content
    sitemap = client.get("/sitemap.xml").content.decode()
    assert job.get_absolute_url() in sitemap and "/pricing/" not in sitemap
    assert client.get(job.get_apply_url())["Location"] == "https://mercor.example/jobs/1"


def test_closed_and_expired_jobs_are_excluded_from_open_counts(client, make_job, platform):
    make_job(title="Open", application_url="https://p.example/a")
    make_job(title="Closed", application_url="https://p.example/b", status=Job.Status.CLOSED)
    make_job(title="Draft", application_url="https://p.example/c", status=Job.Status.DRAFT)
    make_job(title="Expired by date", application_url="https://p.example/d", expires_at=timezone.now() - timedelta(minutes=1))
    make_job(title="Expired", application_url="https://p.example/e", status=Job.Status.EXPIRED)
    assert directory_stats()["total"] == 1
    assert client.get(reverse("jobs:list")).context["total"] == 1
    assert {p.name: p.open_jobs for p in client.get(reverse("jobs:platform_list")).context["platforms"]}[platform.name] == 1


def test_referral_program_controls_apply_redirect(client, make_job, platform):
    job = make_job(referral_url="https://platform.example.com/apply/1?ref=me")
    go = job.get_apply_url()
    assert client.get(go)["Location"] == job.application_url  # no active program → plain link
    program = ReferralProgram.objects.create(
        platform=platform, approved_referral_url="https://platform.example.com/join?ref=me", status=ReferralProgram.Status.ACTIVE
    )
    assert client.get(go)["Location"] == "https://platform.example.com/apply/1?ref=me"
    assert ClickEvent.objects.filter(link_type="referral").count() == 1
    assert b"Referral link" in client.get(job.get_absolute_url()).content
    program.status = ReferralProgram.Status.PAUSED
    program.save()
    assert client.get(go)["Location"] == job.application_url


# --- Statistics -----------------------------------------------------------------------------------


def test_directory_stats_come_from_real_rows(make_job, platform):
    other = Platform.objects.create(name="Micro1", slug="micro1", website="https://micro1.example")
    now = timezone.now()
    make_job(title="a", application_url="https://p.example/a", last_verified_at=now, published_at=now - timedelta(hours=3))
    make_job(title="b", application_url="https://p.example/b", last_verified_at=now - timedelta(days=60), published_at=now - timedelta(hours=2))
    make_job(title="c", application_url="https://p.example/c", platform=other, published_at=now - timedelta(hours=1))
    make_job(title="d", application_url="https://p.example/d", status=Job.Status.CLOSED)
    make_job(title="e", application_url="https://p.example/e", expires_at=now - timedelta(days=1))
    make_job(title="f", application_url="https://p.example/f", status=Job.Status.DRAFT)
    s = directory_stats()
    assert s["total"] == 3 and s["companies"] == 2
    assert [(r["platform__name"], r["n"], r["pct"]) for r in s["by_company"]] == [("TestPlatform", 2, 66.7), ("Micro1", 1, 33.3)]
    assert (s["verified_open"], s["unverified_open"], s["closed"]) == (1, 2, 2)
    assert [j.title for j in s["recent"]] == ["c", "b", "a"]


def test_home_shows_stats_with_honest_wording(client, make_job):
    make_job()
    page = client.get("/").content
    assert b"Jobs listed here" in page and b"not the number of open vacancies" in page
