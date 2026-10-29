"""CV builder pages. The generated CV lives only in the user's session until they download or discard it."""

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from ai.service import ai_is_configured
from billing import credits
from billing.providers import BillingNotConfigured, BillingProviderError, get_provider
from core.audit import record
from core.models import AuditEvent
from core.ratelimit import ratelimit
from jobs.models import Job

from . import builder, exports
from .schemas import EducationItem, RenderedCV

SESSION_KEY = "cv_builder_result"


class BuilderForm(forms.Form):
    mode = forms.ChoiceField(choices=[(builder.MODE_GENERAL, "Create a CV"), (builder.MODE_JD, "Create CV from job description")])
    target_title = forms.CharField(
        max_length=200,
        required=False,
        label="Target job title",
        widget=forms.TextInput(attrs={"class": "input", "placeholder": "e.g. AI Trainer — Python"}),
    )
    target_company = forms.CharField(
        max_length=150, required=False, label="Company", widget=forms.TextInput(attrs={"class": "input", "placeholder": "Optional"})
    )
    job_description = forms.CharField(
        required=False,
        max_length=settings.JOB_DESCRIPTION_MAX_CHARS,
        label="Job description",
        widget=forms.Textarea(attrs={"class": "textarea text-sm", "rows": 10, "placeholder": "Paste the full job description here"}),
    )
    ai_consent = forms.BooleanField(
        required=False,
        label="I agree that my profile details (and any job description I paste) are sent to our AI provider, OpenAI, to write my CV.",
        widget=forms.CheckboxInput(attrs={"class": "checkbox"}),
    )


def _context(request, form=None):
    user = request.user
    ctx = {
        "packs": credits.packs(),
        "payment_mode": credits.payment_mode(),
        "ai_configured": ai_is_configured(),
        "meta_title": "CV Builder — professional, ATS-friendly CVs",
        "meta_description": "Turn your saved experience into a clean, ATS-friendly CV, or tailor it to a job description. Never invents experience.",
    }
    if not user.is_authenticated:
        return ctx
    initial = {}
    job_id = request.GET.get("job", "")
    if job_id.isdigit():
        job = Job.objects.public().filter(pk=int(job_id)).select_related("platform").first()
        if job:
            initial = {
                "mode": builder.MODE_JD,
                "target_title": job.title,
                "target_company": job.display_company,
                "job_description": "\n\n".join(filter(None, [job.title, job.description, job.preferred_qualifications])),
            }
    ctx.update(
        {
            "form": form or BuilderForm(initial=initial or {"mode": builder.MODE_GENERAL}),
            "balance": credits.balance(user),
            "problems": builder.profile_problems(user),
            "profile": user.profile,
            "experiences": list(user.work_experiences.all()[:6]),
            "needs_consent": not user.profile.ai_processing_consent_at,
            "has_result": bool(request.session.get(SESSION_KEY)),
        }
    )
    return ctx


def builder_home(request):
    return render(request, "cvs/builder.html", _context(request))


@login_required
@require_POST
@ratelimit("cv_builder", limit=10, window_seconds=600, by="user")
def builder_generate(request):
    user = request.user
    form = BuilderForm(request.POST)
    if not form.is_valid():
        return render(request, "cvs/builder.html", _context(request, form), status=400)
    data = form.cleaned_data
    if not user.profile.ai_processing_consent_at:
        if not data.get("ai_consent"):
            form.add_error("ai_consent", "Please confirm you agree to AI processing.")
            return render(request, "cvs/builder.html", _context(request, form), status=400)
        user.profile.ai_processing_consent_at = timezone.now()
        user.profile.save(update_fields=["ai_processing_consent_at"])
    try:
        result = builder.generate(
            user,
            mode=data["mode"],
            target_title=data["target_title"],
            target_company=data["target_company"],
            job_description=data["job_description"],
        )
    except credits.InsufficientCredits:
        messages.error(request, "You need a credit to generate a CV. Choose a credit pack below.")
        return render(request, "cvs/builder.html", _context(request, form), status=402)
    except builder.BuilderError as exc:
        messages.error(request, str(exc))
        return render(request, "cvs/builder.html", _context(request, form), status=400)
    request.session[SESSION_KEY] = result
    messages.success(request, "Your CV is ready. Review it, make any edits, then download.")
    return redirect("cvs:builder_result")


def _lines(text):
    return [ln.strip().lstrip("•-*· ").strip() for ln in (text or "").splitlines() if ln.strip().lstrip("•-*· ").strip()]


def _session_result(request):
    data = request.session.get(SESSION_KEY)
    if not data:
        return None, None
    return data, RenderedCV.model_validate(data["content"])


@login_required
def builder_result(request):
    data, cv = _session_result(request)
    if data is None:
        messages.info(request, "Generate a CV first — results are kept only until you close your session.")
        return redirect("cvs:builder")
    if request.method == "POST":
        p = request.POST
        cv.headline = p.get("headline", cv.headline).strip()[:200]
        cv.summary = p.get("summary", cv.summary).strip()[:3000]
        cv.skills = [s.strip()[:60] for s in p.get("skills", "").split(",") if s.strip()][:60]
        for e in cv.experience:
            if f"exp_{e.id}" in p:
                e.bullets = _lines(p.get(f"exp_{e.id}"))[:12]
        if "education" in p:
            cv.education = [
                EducationItem(id=f"edu{i}", institution="", qualification=line) for i, line in enumerate(_lines(p["education"])[:20], start=1)
            ]
        for s in cv.other_sections:
            key = f"other_{slugify(s.title)}"
            if key in p:
                s.items = _lines(p.get(key))[:30]
        data["content"] = cv.model_dump()
        request.session[SESSION_KEY] = data
        messages.success(request, "Changes saved.")
        return redirect("cvs:builder_result")
    return render(
        request,
        "cvs/builder_result.html",
        {
            "data": data,
            "cv": cv,
            "meta_title": "Your CV",
            "noindex": True,
            "education_lines": [e.qualification for e in cv.education],
            "other_fields": [(s, f"other_{slugify(s.title)}") for s in cv.other_sections],
        },
    )


@login_required
def builder_download(request, fmt):
    data, cv = _session_result(request)
    if data is None:
        return redirect("cvs:builder")
    base = slugify(" ".join(filter(None, [cv.full_name or "cv", data.get("target_title", "")])))[:80] or "cv"
    if fmt == "pdf":
        body, ctype = exports.render_pdf(cv), "application/pdf"
    elif fmt == "docx":
        body, ctype = exports.render_docx(cv), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    else:
        raise Http404
    resp = HttpResponse(body, content_type=ctype)
    resp["Content-Disposition"] = f'attachment; filename="{base}.{fmt}"'
    resp["Cache-Control"] = "private, no-store"
    return resp


@login_required
@require_POST
def builder_discard(request):
    request.session.pop(SESSION_KEY, None)
    messages.success(request, "CV discarded. Nothing was saved on our servers.")
    return redirect("cvs:builder")


@login_required
@require_POST
@ratelimit("buy_credits", limit=10, window_seconds=600, by="user")
def buy_credits(request, code):
    pack = credits.get_pack(code)
    if pack is None:
        raise Http404
    mode = credits.payment_mode()
    if mode == "test":
        credits.grant(request.user, pack.credits, credits.CreditTransaction.Kind.TEST_GRANT, note=f"test pack={pack.code}")
        record("test_credits_granted", actor=request.user, target=request.user, category=AuditEvent.Category.BILLING, pack=pack.code)
        messages.success(request, f"Test mode: {pack.credits} credit{'s' if pack.credits != 1 else ''} added. No payment was taken.")
        return redirect(reverse("cvs:builder") + "#credits")
    if mode == "off":
        messages.error(request, "Online payments aren't set up yet. Please check back soon.")
        return redirect(reverse("cvs:builder") + "#credits")
    try:
        url = get_provider().create_credit_checkout_url(
            user=request.user,
            pack=pack,
            success_url=settings.SITE_URL + reverse("cvs:builder_purchase_done"),
            cancel_url=settings.SITE_URL + reverse("cvs:builder") + "#credits",
        )
    except (BillingNotConfigured, BillingProviderError):
        messages.error(request, "We couldn't start the payment. Please try again in a moment.")
        return redirect(reverse("cvs:builder") + "#credits")
    record("credit_checkout_started", actor=request.user, target=request.user, category=AuditEvent.Category.BILLING, pack=pack.code)
    return redirect(url)


@login_required
def purchase_done(request):
    # The browser redirect is not proof of payment — only the signed webhook adds credits.
    messages.info(request, "Thanks! Your credits appear as soon as the payment is confirmed (usually within a few seconds). Refresh if needed.")
    return redirect(reverse("cvs:builder") + "#credits")
