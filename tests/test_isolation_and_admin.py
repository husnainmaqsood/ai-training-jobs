import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


def test_users_cannot_access_other_users_cvs(client, other_user, master_cv):
    client.force_login(other_user)
    for name in ["cvs:master_detail", "cvs:master_download"]:
        assert client.get(reverse(name, args=[master_cv.public_id])).status_code == 404
    assert client.post(reverse("cvs:master_delete", args=[master_cv.public_id])).status_code == 404
    from cvs.models import MasterCV

    assert MasterCV.objects.filter(pk=master_cv.pk).exists()


def test_users_cannot_access_other_users_generations(client, user, other_user, master_cv):
    from cvs.services import start_generation

    gen = start_generation(
        user=user,
        master=master_cv,
        target_title="Python AI Trainer",
        job_description="We need Python and SQL experience for evaluating AI model code. " * 3,
    )
    gcv = gen.versions.first()
    client.force_login(other_user)
    assert client.get(reverse("cvs:generation_detail", args=[gen.public_id])).status_code == 404
    assert client.get(reverse("cvs:generated_detail", args=[gcv.public_id])).status_code == 404
    assert client.get(reverse("cvs:generated_download", args=[gcv.public_id, "pdf"])).status_code == 404
    assert client.post(reverse("cvs:generated_delete", args=[gcv.public_id])).status_code == 404


def test_cannot_tailor_with_someone_elses_cv(client, other_user, master_cv, plans):
    client.force_login(other_user)
    other_user.profile.ai_processing_consent_at = master_cv.created_at
    other_user.profile.save()
    resp = client.post(reverse("cvs:tailor"), {"master_cv": master_cv.pk, "target_title": "x", "job_description": "y" * 200})
    assert resp.status_code == 200  # form invalid: CV not in user's queryset
    from cvs.models import GenerationRequest

    assert not GenerationRequest.objects.exists()


def test_users_cannot_access_other_users_applications(client, user, other_user):
    from tracker.models import Application

    app = Application.objects.create(user=user, title="Secret", company="X")
    client.force_login(other_user)
    assert client.get(reverse("tracker:detail", args=[app.pk])).status_code == 404
    assert client.post(reverse("tracker:delete", args=[app.pk])).status_code == 404
    assert client.post(reverse("tracker:status", args=[app.pk]), {"status": "offer"}).status_code == 404


def test_application_form_rejects_foreign_cv_version(client, user, other_user, master_cv):
    from cvs.services import start_generation

    gen = start_generation(user=user, master=master_cv, target_title="T", job_description="Python SQL evaluation work. " * 10)
    gcv = gen.versions.first()
    client.force_login(other_user)
    resp = client.post(reverse("tracker:create"), {"title": "Mine", "status": "saved", "generated_cv": gcv.pk})
    assert resp.status_code == 200  # invalid choice
    from tracker.models import Application

    assert not Application.objects.filter(user=other_user).exists()


def test_django_admin_forbidden_to_regular_users_and_visitors(client, client_user):
    for c in (client_user, client.__class__()):
        for url in ["/admin/", "/admin/jobs/job/", "/admin/jobs/importrun/"]:
            resp = c.get(url)
            assert resp.status_code == 302 and "/admin/login/" in resp["Location"], url


def test_django_admin_available_to_the_admin(client, staff_user, plans):
    client.force_login(staff_user)
    for url in ["/admin/", "/admin/jobs/job/", "/admin/jobs/importrun/", "/admin/jobs/platform/", "/admin/jobs/category/", "/admin/jobs/job/import/"]:
        assert client.get(url).status_code == 200, url


def test_admin_changes_are_audited(client, staff_user, platform):
    client.force_login(staff_user)
    resp = client.post(
        f"/admin/jobs/platform/{platform.pk}/change/",
        {
            "name": "Renamed",
            "slug": platform.slug,
            "website": platform.website,
            "is_active": "on",
        },
    )
    assert resp.status_code == 302
    from core.models import AuditEvent

    ev = AuditEvent.objects.get(action="admin_update")
    assert ev.actor == staff_user and "name" in ev.details["changed"]


def test_admin_cannot_read_cv_text(client, staff_user, master_cv):
    client.force_login(staff_user)
    resp = client.get(f"/admin/cvs/mastercv/{master_cv.pk}/change/")
    assert resp.status_code == 200
    assert b"Example Analytics" not in resp.content


def test_purge_expired_data(make_job):
    from datetime import timedelta

    from django.core.management import call_command
    from django.utils import timezone

    from referrals.models import ClickEvent

    job = make_job()
    old = ClickEvent.objects.create(job=job, link_type="application", destination=job.application_url, visitor_hash="x" * 64)
    ClickEvent.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=500))
    ClickEvent.objects.create(job=job, link_type="application", destination=job.application_url, visitor_hash="y" * 64)
    call_command("purge_expired_data", stdout=__import__("io").StringIO())
    assert ClickEvent.objects.count() == 1
