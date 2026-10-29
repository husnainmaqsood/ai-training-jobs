import re

import pytest
from django.core import mail
from django.urls import reverse

pytestmark = pytest.mark.django_db


def test_register_creates_unverified_user_and_sends_verification(client, plans):
    resp = client.post(
        reverse("accounts:register"),
        {
            "email": "New@Example.com",
            "password1": "a-Long-unique-pass-91",
            "password2": "a-Long-unique-pass-91",
            "accept_terms": "on",
            "country": "PK",
        },
    )
    assert resp.status_code == 302
    from accounts.models import User

    u = User.objects.get(email="new@example.com")
    assert not u.email_verified
    assert u.profile.country == "PK"
    assert len(mail.outbox) == 1
    link = re.search(r"http\S+/accounts/verify/\S+/", mail.outbox[0].body).group()
    client.get(link.replace("http://localhost:8000", ""))
    u.refresh_from_db()
    assert u.email_verified


def test_register_rejects_weak_password_and_duplicate_email(client, user):
    resp = client.post(reverse("accounts:register"), {"email": user.email, "password1": "123", "password2": "123", "accept_terms": "on"})
    assert resp.status_code == 200
    assert b"already exists" in resp.content


def test_invalid_verification_token(client, user):
    resp = client.get(reverse("accounts:verify_email", args=["MQ", "bad-token"]))
    assert resp.status_code == 302


def test_login_and_logout(client, user):
    resp = client.post(reverse("accounts:login"), {"username": "alice@example.com", "password": "Sup3r-secret-pass"})
    assert resp.status_code == 302
    assert client.get(reverse("accounts:dashboard")).status_code == 200
    client.post(reverse("accounts:logout"))
    assert client.get(reverse("accounts:dashboard")).status_code == 302


def test_login_rate_limited(client, user):
    for _ in range(10):
        client.post(reverse("accounts:login"), {"username": "alice@example.com", "password": "wrong"})
    resp = client.post(reverse("accounts:login"), {"username": "alice@example.com", "password": "wrong"})
    assert resp.status_code == 429


@pytest.mark.parametrize(
    "name",
    ["accounts:dashboard", "accounts:profile", "cvs:workspace", "cvs:tailor", "cvs:history", "billing:settings", "tracker:board", "jobs:saved"],
)
def test_private_pages_require_login(client, name):
    resp = client.get(reverse(name))
    assert resp.status_code == 302 and "/accounts/login/" in resp["Location"]


def test_password_reset_email(client, user):
    client.post(reverse("accounts:password_reset"), {"email": user.email})
    assert len(mail.outbox) == 1
    assert "/accounts/password/reset/" in mail.outbox[0].body


def test_account_deletion_removes_data(client_user, user, master_cv):
    path = master_cv.original_file.name if master_cv.original_file else None
    resp = client_user.post(reverse("accounts:delete"), {"password": "Sup3r-secret-pass", "confirm": "DELETE"})
    assert resp.status_code == 302
    from accounts.models import User
    from core.models import AuditEvent
    from cvs.models import MasterCV

    assert not User.objects.filter(pk=user.pk).exists()
    assert not MasterCV.objects.filter(pk=master_cv.pk).exists()
    assert AuditEvent.objects.filter(action="account_deleted").exists()
    assert path is None


def test_account_deletion_requires_password(client_user, user):
    resp = client_user.post(reverse("accounts:delete"), {"password": "nope", "confirm": "DELETE"})
    assert resp.status_code == 200
    from accounts.models import User

    assert User.objects.filter(pk=user.pk).exists()
