from django.urls import path

from . import views

app_name = "billing"

urlpatterns = [
    path("pricing/", views.pricing, name="pricing"),
    path("billing/", views.billing_settings, name="settings"),
    path("billing/checkout/<slug:code>/", views.checkout, name="checkout"),
    path("billing/success/", views.checkout_success, name="success"),
    path("billing/cancelled/", views.checkout_cancelled, name="cancelled"),
    path("billing/portal/", views.portal, name="portal"),
    path("billing/cancel/", views.cancel, name="cancel"),
    path("billing/resume/", views.resume, name="resume"),
    path("billing/webhooks/stripe/", views.stripe_webhook, name="stripe_webhook"),
]
