import pytest
from django.urls import reverse

from referrals.models import ClickEvent, ReferralProgram

pytestmark = pytest.mark.django_db


def test_direct_application_link_when_no_program(client, make_job):
    job = make_job()
    resp = client.get(reverse("referrals:go_job", args=[job.pk]) + "?from=job_detail", HTTP_USER_AGENT="UA", REMOTE_ADDR="1.2.3.4")
    assert resp.status_code == 302 and resp["Location"] == job.application_url
    click = ClickEvent.objects.get()
    assert click.link_type == "application" and click.job == job and click.page == "job_detail"
    # no raw personal data stored
    assert "1.2.3.4" not in click.visitor_hash and len(click.visitor_hash) == 64


def test_referral_used_only_with_active_program(client, make_job, platform):
    job = make_job(referral_url="https://platform.example.com/r/abc")
    program = ReferralProgram.objects.create(platform=platform, status="unverified", approved_referral_url="https://platform.example.com/r/abc")
    resp = client.get(reverse("referrals:go_job", args=[job.pk]))
    assert resp["Location"] == job.application_url  # unverified program → never use referral

    program.status = "active"
    program.save()
    resp = client.get(reverse("referrals:go_job", args=[job.pk]))
    assert resp["Location"] == "https://platform.example.com/r/abc"
    assert ClickEvent.objects.filter(link_type="referral", program=program).count() == 1


def test_tracking_template(client, make_job, platform):
    job = make_job()
    ReferralProgram.objects.create(platform=platform, status="active", tracking_template="{url}?ref=OURCODE")
    resp = client.get(reverse("referrals:go_job", args=[job.pk]))
    assert resp["Location"] == job.application_url + "?ref=OURCODE"


def test_referral_disclosure_shown_on_job_page(client, make_job, platform):
    job = make_job(referral_url="https://platform.example.com/r/abc")
    ReferralProgram.objects.create(platform=platform, status="active", approved_referral_url="https://platform.example.com/r/abc")
    content = client.get(job.get_absolute_url()).content
    assert b"Referral link" in content and b"commission" in content


def test_no_open_redirect(client, make_job):
    job = make_job()
    resp = client.get(reverse("referrals:go_job", args=[job.pk]) + "?next=https://evil.example.com&url=https://evil.example.com")
    assert resp["Location"] == job.application_url


def test_unpublished_job_does_not_redirect_offsite(client, make_job):
    job = make_job(status="draft")
    resp = client.get(reverse("referrals:go_job", args=[job.pk]))
    assert resp["Location"] == job.get_absolute_url()
    assert not ClickEvent.objects.exists()


def test_admin_rejects_referral_url_without_active_program(platform):
    from jobs.admin import JobAdminForm

    form = JobAdminForm(
        data={
            "title": "x",
            "platform": platform.pk,
            "description": "d" * 50,
            "application_url": "https://a.example.com",
            "referral_url": "https://a.example.com/ref",
            "employment_type": "contract",
            "work_arrangement": "remote",
            "eligibility_scope": "worldwide",
            "experience_level": "any",
            "status": "draft",
        }
    )
    assert not form.is_valid() and "referral_url" in form.errors


def test_conversion_reporting_separates_states(platform):
    from datetime import date

    from referrals.models import ReferralConversion, ReferralPayout

    prog = ReferralProgram.objects.create(platform=platform, status="active", approved_referral_url="https://x.example.com")
    payout = ReferralPayout.objects.create(program=prog, amount=50, paid_at=date.today())
    ReferralConversion.objects.create(program=prog, external_reference="a", status="reported", reported_at=date.today())
    ReferralConversion.objects.create(program=prog, external_reference="b", status="approved", amount=50, reported_at=date.today())
    ReferralConversion.objects.create(program=prog, external_reference="c", status="paid", amount=50, payout=payout, reported_at=date.today())
    assert {c.status for c in ReferralConversion.objects.all()} == {"reported", "approved", "paid"}
