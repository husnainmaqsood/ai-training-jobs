from django.contrib import admin
from django.contrib.sitemaps.views import sitemap
from django.urls import include, path

from core.sitemaps import sitemaps

admin.site.site_header = "Orexia AI — Admin"
admin.site.site_title = "Orexia AI admin"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("core.urls")),
    path("accounts/", include("accounts.urls")),
    path("", include("jobs.urls")),
    path("go/", include("referrals.urls")),
    path("cv/", include("cvs.urls")),
    path("", include("billing.urls")),
    path("applications/", include("tracker.urls")),
    path("sitemap.xml", sitemap, {"sitemaps": sitemaps}, name="django.contrib.sitemaps.views.sitemap"),
]

handler404 = "core.errors.not_found"
handler500 = "core.errors.server_error"
