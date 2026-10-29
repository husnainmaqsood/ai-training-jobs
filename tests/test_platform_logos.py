import html
import io
from unittest import mock

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.urls import reverse
from PIL import Image

from jobs import logos
from jobs.models import Platform, PlatformLogo

pytestmark = pytest.mark.django_db


def png_bytes(size=(120, 80), colour=(40, 90, 200, 255)):
    buf = io.BytesIO()
    Image.new("RGBA", size, colour).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def admin_client(client, staff_user):
    client.force_login(staff_user)
    return client


def change(admin_client, platform, **extra):
    data = {"name": platform.name, "slug": platform.slug, "website": platform.website, "careers_url": "", "is_active": "on"}
    data.update(extra)
    return admin_client.post(reverse("admin:jobs_platform_change", args=[platform.pk]), data)


def test_admin_uploads_a_logo_and_the_site_shows_it(admin_client, client, platform, make_job):
    resp = change(admin_client, platform, logo_upload=SimpleUploadedFile("logo.png", png_bytes((600, 400))))
    assert resp.status_code == 302
    platform.refresh_from_db()
    assert platform.logo_version and platform.logo.source == "upload: logo.png"
    stored = Image.open(io.BytesIO(bytes(platform.logo.image)))
    assert stored.format == "PNG" and max(stored.size) == logos.MAX_PX  # resized

    make_job()
    page = client.get(reverse("jobs:list")).content.decode()
    assert platform.logo_url in page
    served = client.get(platform.logo_url)
    assert served.status_code == 200 and served["Content-Type"] == "image/png"
    assert "immutable" in served["Cache-Control"] and served["X-Content-Type-Options"] == "nosniff"
    assert client.get(platform.logo_url, HTTP_IF_NONE_MATCH=served["ETag"]).status_code == 304


def test_platform_without_logo_shows_initials(client, platform, make_job):
    make_job()
    page = client.get(reverse("jobs:list")).content.decode()
    assert "/logo.png" not in page and ">Te<" in page
    assert client.get(reverse("jobs:platform_logo", args=[platform.slug])).status_code == 404


@pytest.mark.parametrize(
    "upload,message",
    [
        (SimpleUploadedFile("logo.svg", b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'), "SVG isn't supported"),
        (SimpleUploadedFile("tiny.png", png_bytes((10, 10))), "too small"),
        (SimpleUploadedFile("notes.txt", b"hello"), "isn't a readable image"),
    ],
)
def test_bad_logo_uploads_are_rejected(admin_client, platform, upload, message):
    resp = change(admin_client, platform, logo_upload=upload)
    assert resp.status_code == 200 and message in html.unescape(resp.content.decode())
    assert not PlatformLogo.objects.exists()


def test_remove_logo(admin_client, platform):
    logos.save_logo(platform, png_bytes())
    change(admin_client, platform, remove_logo="on")
    platform.refresh_from_db()
    assert platform.logo_version == "" and not PlatformLogo.objects.exists()


def test_replacing_a_logo_changes_its_url(platform):
    logos.save_logo(platform, png_bytes(colour=(255, 0, 0, 255)))
    first = Platform.objects.get(pk=platform.pk).logo_url
    logos.save_logo(platform, png_bytes(colour=(0, 255, 0, 255)))
    assert Platform.objects.get(pk=platform.pk).logo_url != first
    assert PlatformLogo.objects.count() == 1


def test_fetch_logo_from_website_prefers_the_declared_large_icon(platform):
    html = b'<html><head><link rel="icon" href="/favicon.ico"><link rel="apple-touch-icon" sizes="180x180" href="/touch.png"><link rel="icon" href="/logo.svg"></head></html>'
    fetched = []

    def fake_get(url):
        fetched.append(url)
        if url == platform.website:
            return url, html
        if url.endswith("/touch.png"):
            return url, png_bytes((180, 180))
        raise logos.LogoError("nope")

    with mock.patch.object(logos, "_get", side_effect=fake_get):
        logos.fetch_and_save(platform)
    platform.refresh_from_db()
    assert platform.logo.source.endswith("/touch.png")
    assert not any(u.endswith(".svg") for u in fetched)


def test_fetch_refuses_placeholder_websites(platform):
    platform.website = "https://example.com"
    with pytest.raises(logos.LogoError, match="no real website"):
        logos.fetch_logo(platform)


def test_fetch_command_reports_each_platform(platform):
    other = Platform.objects.create(name="Nowhere", slug="nowhere", website="https://example.com")
    out = io.StringIO()
    with mock.patch.object(
        logos,
        "fetch_logo",
        side_effect=lambda p: (png_bytes(), "https://x/icon.png") if p == platform else (_ for _ in ()).throw(logos.LogoError("no site")),
    ):
        call_command("fetch_platform_logos", stdout=out)
    assert "✓ TestPlatform" in out.getvalue() and "✗ Nowhere" in out.getvalue()
    assert Platform.objects.get(pk=platform.pk).logo_version and not Platform.objects.get(pk=other.pk).logo_version


def test_admin_list_shows_logo_column_and_fetch_action(admin_client, platform):
    logos.save_logo(platform, png_bytes())
    page = admin_client.get(reverse("admin:jobs_platform_changelist")).content.decode()
    assert platform.logo_url.split("?")[0] in page and "Fetch logos from their websites" in page
