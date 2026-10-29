from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from billing import entitlements
from core.countries import COUNTRIES, country_name
from core.ratelimit import ratelimit
from referrals.services import active_program, job_uses_referral

from .forms import JobFilterForm, JobReportForm
from .models import Category, Job, Platform, PlatformLogo, SavedJob, Skill, logo_url
from .search import filter_jobs

PAGE_SIZE = 20
# Landing pages with fewer listings than this are marked noindex to avoid thin content.
MIN_INDEXABLE = 3


def _base_qs():
    return Job.objects.public().select_related("platform", "category").prefetch_related("skills")


def _render_list(request, qs, *, title, description, heading, landing=None, initial=None):
    data = request.GET.copy()
    for k, v in (initial or {}).items():
        data.setdefault(k, v)
    form = JobFilterForm(data)
    cleaned = form.cleaned_data if form.is_valid() else {}
    qs, _ = filter_jobs(qs, cleaned)
    page = Paginator(qs, PAGE_SIZE).get_page(request.GET.get("page"))
    saved_ids = set()
    if request.user.is_authenticated:
        saved_ids = set(SavedJob.objects.filter(user=request.user, job__in=page.object_list).values_list("job_id", flat=True))
    query = request.GET.copy()
    query.pop("page", None)
    context = {
        "form": form,
        "page": page,
        "jobs": page.object_list,
        "saved_ids": saved_ids,
        "querystring": query.urlencode(),
        "meta_title": title,
        "meta_description": description,
        "heading": heading,
        "landing": landing,
        "noindex": bool(request.GET.get("q")) or page.paginator.count < MIN_INDEXABLE and landing is not None,
        "total": page.paginator.count,
        "companies": [
            {**row, "logo": logo_url(row["platform__slug"], row["platform__logo_version"])}
            for row in Job.objects.public()
            .values("platform__slug", "platform__name", "platform__logo_version")
            .annotate(n=Count("id"))
            .order_by("-n", "platform__name")
        ],
        "active_filters": sum(
            bool(cleaned.get(k)) for k in ("country", "employment_type", "min_hourly", "worldwide_only", "category", "skill", "experience")
        ),
    }
    template = "jobs/_results.html" if request.headers.get("HX-Request") else "jobs/list.html"
    return render(request, template, context)


def job_list(request):
    return _render_list(
        request,
        _base_qs(),
        title="Explore remote AI training & LLM jobs",
        description="Browse remote AI-training and LLM jobs from Mercor, Micro1, Turing, DataAnnotation and more — with rates and direct application links.",
        heading="Explore jobs",
    )


def category_jobs(request, slug):
    category = get_object_or_404(Category, slug=slug)
    return _render_list(
        request,
        _base_qs().filter(category=category),
        title=f"{category.name} jobs (remote)",
        description=category.description[:155] or f"Remote {category.name} opportunities.",
        heading=f"{category.name} jobs",
        landing=category,
    )


def skill_jobs(request, slug):
    skill = get_object_or_404(Skill, slug=slug)
    return _render_list(
        request,
        _base_qs().filter(skills=skill),
        title=f"Remote AI training jobs requiring {skill.name}",
        description=f"Remote AI-training and evaluation opportunities that ask for {skill.name}.",
        heading=f"Jobs requiring {skill.name}",
        landing=skill,
    )


def country_jobs(request, code):
    code = code.upper()
    if code not in COUNTRIES:
        raise Http404
    name = country_name(code)
    return _render_list(
        request,
        _base_qs().eligible_for_country(code),
        title=f"Remote AI training jobs open to applicants in {name}",
        description=f"Remote AI-training opportunities whose listed eligibility includes {name} — worldwide roles and roles specifically open to {name}.",
        heading=f"Remote AI jobs open to {name}",
        landing={"name": name, "code": code},
    )


def job_detail(request, pk, slug=None):
    job = get_object_or_404(Job.objects.select_related("platform", "category", "source").prefetch_related("skills"), pk=pk)
    if not job.is_public and not request.user.is_staff:
        if job.status in {Job.Status.EXPIRED, Job.Status.CLOSED, Job.Status.ARCHIVED}:
            return render(request, "jobs/expired.html", {"job": job}, status=410)
        raise Http404
    if slug != job.slug:
        return redirect(job.get_absolute_url(), permanent=True)
    saved = request.user.is_authenticated and SavedJob.objects.filter(user=request.user, job=job).exists()
    related_qs = _base_qs().filter(category=job.category) if job.category else _base_qs().filter(platform=job.platform)
    related = related_qs.exclude(pk=job.pk)[:4]
    return render(
        request,
        "jobs/detail.html",
        {
            "job": job,
            "saved": saved,
            "related": related,
            "uses_referral": job_uses_referral(job),
            "report_form": JobReportForm(),
            "meta_title": f"{job.title} at {job.display_company}",
            "meta_description": job.description[:155],
        },
    )


@login_required
@require_POST
def toggle_save(request, pk):
    job = get_object_or_404(Job.objects.public(), pk=pk)
    existing = SavedJob.objects.filter(user=request.user, job=job).first()
    if existing:
        existing.delete()
        saved = False
    else:
        count = SavedJob.objects.filter(user=request.user).count()
        if entitlements.limit_reached(request.user, "max_saved_jobs", count):
            msg = "You've reached the saved-jobs limit on your plan. Upgrade to save more."
            if request.headers.get("HX-Request"):
                return render(request, "jobs/_save_button.html", {"job": job, "saved": False, "error": msg})
            messages.error(request, msg)
            return redirect(job.get_absolute_url())
        SavedJob.objects.get_or_create(user=request.user, job=job)
        saved = True
    if request.headers.get("HX-Request"):
        return render(request, "jobs/_save_button.html", {"job": job, "saved": saved})
    return redirect(request.POST.get("next") or job.get_absolute_url())


@require_POST
@ratelimit("job_report", limit=5, window_seconds=3600)
def report_job(request, pk):
    job = get_object_or_404(Job, pk=pk)
    form = JobReportForm(request.POST)
    if form.is_valid():
        report = form.save(commit=False)
        report.job = job
        report.reporter = request.user if request.user.is_authenticated else None
        report.save()
        if job.reports.filter(status="open").count() >= 3:
            Job.objects.filter(pk=job.pk).update(needs_review=True, review_note="Multiple user reports")
        messages.success(request, "Thanks — our team will review this listing.")
    else:
        messages.error(request, "Please choose a reason.")
    return redirect(job.get_absolute_url())


@login_required
def saved_jobs(request):
    saved = SavedJob.objects.filter(user=request.user).select_related("job", "job__platform")
    plan = entitlements.plan_for(request.user)
    return render(request, "jobs/saved.html", {"saved": saved, "plan": plan})


def platform_list(request):
    public_ids = Job.objects.public().values("pk")
    platforms = Platform.objects.filter(is_active=True).annotate(open_jobs=Count("jobs", filter=Q(jobs__in=public_ids), distinct=True))
    return render(
        request,
        "jobs/platforms.html",
        {
            "platforms": platforms,
            "meta_title": "AI training & data-work platforms directory",
            "meta_description": "A directory of AI-training, RLHF and data annotation platforms with links to their official sites and current listings.",
        },
    )


def platform_detail(request, slug):
    platform = get_object_or_404(Platform, slug=slug, is_active=True)
    jobs = _base_qs().filter(platform=platform)[:30]
    return render(
        request,
        "jobs/platform_detail.html",
        {
            "platform": platform,
            "jobs": jobs,
            "program": active_program(platform),
            "meta_title": f"{platform.name} — remote AI training jobs",
            "meta_description": platform.description[:155],
        },
    )


@require_GET
def platform_logo(request, slug):
    """The platform's normalised PNG logo. URLs carry ?v=<hash>, so responses can be cached for a long time."""
    logo = get_object_or_404(PlatformLogo.objects.select_related("platform"), platform__slug=slug)
    etag = f'"{logo.platform.logo_version}"'
    if request.headers.get("If-None-Match") == etag:
        return HttpResponse(status=304)
    resp = HttpResponse(bytes(logo.image), content_type="image/png")
    resp["ETag"] = etag
    resp["Cache-Control"] = "public, max-age=31536000, immutable" if request.GET.get("v") == logo.platform.logo_version else "public, max-age=300"
    resp["X-Content-Type-Options"] = "nosniff"
    resp["Content-Security-Policy"] = "default-src 'none'"
    return resp
