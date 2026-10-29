from django.urls import path, re_path
from django.views.generic import RedirectView

from . import views

app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
    path("how-it-works/", views.how_it_works, name="how_it_works"),
    path("faq/", views.faq, name="faq"),
    path("contact/", views.contact, name="contact"),
    path("privacy/", views.privacy, name="privacy"),
    path("terms/", views.terms, name="terms"),
    path("referral-disclosure/", views.referral_disclosure, name="referral_disclosure"),
    path("newsletter/subscribe/", views.newsletter_subscribe, name="newsletter_subscribe"),
    path("newsletter/confirm/<str:token>/", views.newsletter_confirm, name="newsletter_confirm"),
    path("newsletter/unsubscribe/<str:token>/", views.newsletter_unsubscribe, name="newsletter_unsubscribe"),
    path("robots.txt", views.robots_txt, name="robots"),
    path("healthz", views.healthz, name="healthz"),
    # The old custom admin area; everything is managed in Django Admin now.
    re_path(r"^staff(?:/.*)?$", RedirectView.as_view(url="/admin/", permanent=False)),
]
