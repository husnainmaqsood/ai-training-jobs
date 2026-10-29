import logging

from django import forms
from django.conf import settings
from django.contrib import messages
from django.core.mail import send_mail
from django.db import connection
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from core.countries import COUNTRY_CHOICES
from jobs.models import Job

from .models import ContactMessage, NewsletterSubscription
from .ratelimit import ratelimit

logger = logging.getLogger(__name__)


def home(request):
    from jobs.stats import directory_stats

    return render(
        request,
        "core/home.html",
        {
            "jobs": Job.objects.public().select_related("platform", "category")[:6],
            "stats": directory_stats(),
            "newsletter_form": NewsletterForm(),
            "meta_title": "Remote AI training & LLM jobs from Mercor, Micro1, Turing and more",
            "meta_description": "A curated directory of remote AI-training and LLM jobs with rates and direct links, plus an honest CV builder.",
        },
    )


def page(template, title, description=""):
    def view(request):
        return render(request, template, {"meta_title": title, "meta_description": description})

    view.__name__ = template.replace("/", "_").replace(".html", "")
    return view


how_it_works = page("core/how_it_works.html", "How it works", "Discover jobs, check eligibility, tailor your CV honestly and track applications.")
faq = page("core/faq.html", "Frequently asked questions", "Answers about AI training jobs, CV tailoring, privacy, billing and referral links.")
privacy = page("core/privacy.html", "Privacy policy")
terms = page("core/terms.html", "Terms of service")
referral_disclosure = page("core/referral_disclosure.html", "Referral & affiliate disclosure")


class ContactForm(forms.ModelForm):
    website = forms.CharField(required=False, widget=forms.TextInput(attrs={"tabindex": "-1", "autocomplete": "off"}))  # honeypot

    class Meta:
        model = ContactMessage
        fields = ["name", "email", "subject", "message"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "input"}),
            "email": forms.EmailInput(attrs={"class": "input"}),
            "subject": forms.TextInput(attrs={"class": "input"}),
            "message": forms.Textarea(attrs={"class": "textarea", "rows": 6}),
        }


@ratelimit("contact", limit=5, window_seconds=3600)
def contact(request):
    form = ContactForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if not form.cleaned_data.get("website"):
            msg = form.save()
            try:
                send_mail(
                    f"[Contact] {msg.subject}",
                    f"From: {msg.name} <{msg.email}>\n\n{msg.message}",
                    settings.DEFAULT_FROM_EMAIL,
                    [settings.CONTACT_EMAIL],
                )
            except Exception:  # message is stored either way
                logger.exception("Failed to forward contact message")
        messages.success(request, "Thanks — we'll get back to you by email.")
        return redirect("core:contact")
    return render(request, "core/contact.html", {"form": form, "meta_title": "Contact"})


# --- Newsletter -------------------------------------------------------------------------------

CONSENT_TEXT = "I agree to receive the Orexia AI newsletter and, if selected, job alerts by email. I can unsubscribe at any time."


class NewsletterForm(forms.Form):
    email = forms.EmailField(widget=forms.EmailInput(attrs={"class": "input", "placeholder": "you@example.com"}))
    consent = forms.BooleanField(label=CONSENT_TEXT, widget=forms.CheckboxInput(attrs={"class": "checkbox"}))
    job_alerts = forms.BooleanField(required=False, label="Also send me weekly job alerts", widget=forms.CheckboxInput(attrs={"class": "checkbox"}))
    alert_country = forms.ChoiceField(required=False, choices=[("", "Any country")] + COUNTRY_CHOICES, widget=forms.Select(attrs={"class": "select"}))


@require_POST
@ratelimit("newsletter", limit=5, window_seconds=3600)
def newsletter_subscribe(request):
    form = NewsletterForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Please enter a valid email and tick the consent box.")
        return redirect(request.META.get("HTTP_REFERER") or "core:home")
    email = form.cleaned_data["email"].lower()
    sub, created = NewsletterSubscription.objects.get_or_create(email=email, defaults={"consent_text": CONSENT_TEXT, "consented_at": timezone.now()})
    sub.job_alerts = form.cleaned_data["job_alerts"]
    sub.alert_country = form.cleaned_data["alert_country"]
    sub.consent_text, sub.consented_at, sub.unsubscribed_at = CONSENT_TEXT, timezone.now(), None
    sub.save()
    if not sub.confirmed_at:
        url = settings.SITE_URL + reverse("core:newsletter_confirm", args=[sub.token])
        try:
            send_mail(
                f"Confirm your {settings.SITE_NAME} subscription",
                f"Please confirm your subscription by visiting:\n\n{url}\n\nIf you didn't request this, ignore this email.",
                settings.DEFAULT_FROM_EMAIL,
                [email],
            )
        except Exception:
            logger.exception("Failed to send newsletter confirmation")
    # Same message whether or not the address existed (no account enumeration).
    messages.success(request, "Almost done — check your inbox to confirm your subscription.")
    return redirect(request.META.get("HTTP_REFERER") or "core:home")


def newsletter_confirm(request, token):
    sub = get_object_or_404(NewsletterSubscription, token=token)
    if not sub.confirmed_at:
        sub.confirmed_at = timezone.now()
        sub.save(update_fields=["confirmed_at"])
    messages.success(request, "Subscription confirmed. Thank you!")
    return redirect("core:home")


def newsletter_unsubscribe(request, token):
    sub = get_object_or_404(NewsletterSubscription, token=token)
    if request.method == "POST":
        sub.unsubscribed_at = timezone.now()
        sub.save(update_fields=["unsubscribed_at"])
        messages.success(request, "You've been unsubscribed.")
        return redirect("core:home")
    return render(request, "core/unsubscribe.html", {"sub": sub})


# --- SEO & health ----------------------------------------------------------------------------


@require_GET
def robots_txt(request):
    lines = [
        "User-agent: *",
        "Disallow: /admin/",
        "Disallow: /staff/",
        "Disallow: /accounts/",
        "Allow: /cv/builder/$",
        "Disallow: /cv/",
        "Disallow: /billing/",
        "Disallow: /applications/",
        "Disallow: /go/",
        "Disallow: /jobs/saved/",
        f"Sitemap: {settings.SITE_URL}/sitemap.xml",
    ]
    return HttpResponse("\n".join(lines) + "\n", content_type="text/plain")


@require_GET
def healthz(request):
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
        db = "ok"
    except Exception:
        db = "error"
    status = 200 if db == "ok" else 503
    return JsonResponse({"status": "ok" if status == 200 else "degraded", "database": db}, status=status)
