import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from ai.service import AIError, ai_is_configured
from billing import entitlements
from core.ratelimit import ratelimit
from jobs.models import Job

from . import exports, services
from .forms import EditMasterTextForm, SectionRegenForm, TailorForm, UploadCVForm
from .models import GeneratedCV, GenerationRequest, MasterCV
from .parsing import CVParseError
from .schemas import RenderedCV

# --- Ownership helpers: every private object is fetched filtered by request.user -------------


def _own_master(request, public_id):
    return get_object_or_404(MasterCV, public_id=public_id, user=request.user)


def _visible_generated_ids(user):
    plan = entitlements.plan_for(user)
    return list(GeneratedCV.objects.filter(user=user).order_by("-created_at").values_list("pk", flat=True)[: plan.max_generated_cvs_visible])


def _own_generated(request, public_id):
    gcv = get_object_or_404(
        GeneratedCV.objects.select_related("request", "request__master_cv", "request__job"), public_id=public_id, user=request.user
    )
    if gcv.pk not in _visible_generated_ids(request.user):
        raise Http404("This CV is outside your plan's history limit.")
    return gcv


# --- Workspace -------------------------------------------------------------------------------


@login_required
def workspace(request):
    quota = entitlements.quota_status(request.user)
    return render(
        request,
        "cvs/workspace.html",
        {
            "master_cvs": MasterCV.objects.filter(user=request.user),
            "generations": GenerationRequest.objects.filter(user=request.user).select_related("job")[:5],
            "quota": quota,
            "ai_configured": ai_is_configured(),
        },
    )


@login_required
@ratelimit("cv_upload", limit=20, window_seconds=3600, by="user")
def upload(request):
    plan = entitlements.plan_for(request.user)
    count = MasterCV.objects.filter(user=request.user).count()
    at_limit = count >= plan.max_master_cvs
    form = UploadCVForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and not at_limit and form.is_valid():
        try:
            if form.cleaned_data.get("file"):
                cv = services.create_master_cv_from_upload(
                    request.user, form.cleaned_data["title"], form.cleaned_data["file"], form.cleaned_data["make_primary"]
                )
            else:
                cv = services.create_master_cv_from_text(
                    request.user, form.cleaned_data["title"], form.cleaned_data["text"], form.cleaned_data["make_primary"]
                )
        except CVParseError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "CV saved. Review the extracted text below, then tailor it to a job.")
            return redirect(cv.get_absolute_url())
    elif request.method == "POST" and at_limit:
        messages.error(request, f"Your plan allows {plan.max_master_cvs} saved CV(s). Delete one or upgrade.")
    return render(request, "cvs/upload.html", {"form": form, "at_limit": at_limit, "plan": plan})


@login_required
def master_detail(request, public_id):
    cv = _own_master(request, public_id)
    form = EditMasterTextForm(request.POST or None, initial={"title": cv.title, "raw_text": cv.raw_text})
    if request.method == "POST" and form.is_valid():
        try:
            cv.title = form.cleaned_data["title"]
            services.update_master_text(cv, form.cleaned_data["raw_text"])
            messages.success(request, "CV text updated. Your original uploaded file is unchanged.")
            return redirect(cv.get_absolute_url())
        except CVParseError as exc:
            form.add_error("raw_text", str(exc))
    return render(request, "cvs/master_detail.html", {"cv": cv, "form": form, "structured": cv.structured})


@login_required
@require_POST
def master_make_primary(request, public_id):
    cv = _own_master(request, public_id)
    MasterCV.objects.filter(user=request.user).update(is_primary=False)
    MasterCV.objects.filter(pk=cv.pk).update(is_primary=True)
    return redirect("cvs:workspace")


@login_required
@require_POST
def master_delete(request, public_id):
    cv = _own_master(request, public_id)
    cv.delete()
    messages.success(request, "CV deleted, including the uploaded file and its tailored versions.")
    return redirect("cvs:workspace")


@login_required
def master_download(request, public_id):
    cv = _own_master(request, public_id)
    if not cv.original_file:
        raise Http404
    content_type = "application/pdf" if cv.file_type == "pdf" else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    resp = FileResponse(
        cv.original_file.open("rb"), as_attachment=True, filename=cv.original_filename or f"cv.{cv.file_type}", content_type=content_type
    )
    resp["Cache-Control"] = "private, no-store"
    return resp


# --- Tailoring -----------------------------------------------------------------------------------


@login_required
@ratelimit("tailor", limit=10, window_seconds=600, by="user")
def tailor(request):
    user = request.user
    job = None
    job_id = request.GET.get("job") or request.POST.get("job_id")
    if job_id and str(job_id).isdigit():
        job = Job.objects.public().filter(pk=int(job_id)).select_related("platform").first()
    initial = {}
    if job:
        initial = {
            "target_title": job.title,
            "target_company": job.display_company,
            "job_id": job.pk,
            "job_description": "\n\n".join(filter(None, [job.description, job.preferred_qualifications])),
        }
    primary = MasterCV.objects.filter(user=user, is_primary=True).first()
    if primary:
        initial["master_cv"] = primary.pk
    form = TailorForm(user, request.POST or None, initial=initial)
    quota = entitlements.quota_status(user)

    if request.method == "POST" and form.is_valid():
        if not user.email_verified:
            messages.error(request, "Please verify your email address before generating CVs.")
        else:
            if form.cleaned_data.get("ai_consent") and not user.profile.ai_processing_consent_at:
                user.profile.ai_processing_consent_at = timezone.now()
                user.profile.save(update_fields=["ai_processing_consent_at"])
            try:
                gen = services.start_generation(
                    user=user,
                    master=form.cleaned_data["master_cv"],
                    job=job,
                    target_title=form.cleaned_data["target_title"],
                    target_company=form.cleaned_data["target_company"],
                    job_description=form.cleaned_data["job_description"],
                )
                return redirect(gen.get_absolute_url())
            except entitlements.QuotaExceeded as exc:
                messages.error(request, f"{exc} Your allowance resets on {quota.period.end:%d %b %Y}.")
            except services.GenerationRejected as exc:
                messages.error(request, str(exc))
    return render(
        request,
        "cvs/tailor.html",
        {
            "form": form,
            "job": job,
            "quota": quota,
            "has_cv": MasterCV.objects.filter(user=user).exists(),
            "ai_configured": ai_is_configured(),
            "needs_consent": not user.profile.ai_processing_consent_at,
        },
    )


@login_required
def generation_detail(request, public_id):
    gen = get_object_or_404(GenerationRequest, public_id=public_id, user=request.user)
    if gen.status == GenerationRequest.Status.SUCCEEDED and gen.versions.exists():
        return redirect(gen.versions.order_by("-version").first().get_absolute_url())
    return render(request, "cvs/generation_status.html", {"gen": gen})


@login_required
def generation_status(request, public_id):
    """HTMX polling endpoint."""
    gen = get_object_or_404(GenerationRequest, public_id=public_id, user=request.user)
    if gen.status == GenerationRequest.Status.SUCCEEDED and gen.versions.exists():
        resp = HttpResponse(status=204)
        resp["HX-Redirect"] = gen.versions.order_by("-version").first().get_absolute_url()
        return resp
    # 286 tells HTMX to stop polling once the generation has finished (e.g. failed).
    return render(request, "cvs/_generation_progress.html", {"gen": gen}, status=286 if gen.is_finished else 200)


@login_required
@require_POST
def generation_retry(request, public_id):
    """Start a new generation with the same inputs (consumes quota like any generation)."""
    gen = get_object_or_404(GenerationRequest, public_id=public_id, user=request.user)
    if not request.user.email_verified:
        messages.error(request, "Please verify your email address first.")
        return redirect(gen.get_absolute_url())
    try:
        new = services.start_generation(
            user=request.user,
            master=gen.master_cv,
            job=gen.job,
            target_title=gen.target_title,
            target_company=gen.target_company,
            job_description=gen.job_description,
        )
        return redirect(new.get_absolute_url())
    except entitlements.QuotaExceeded as exc:
        messages.error(request, str(exc))
    except services.GenerationRejected as exc:
        messages.error(request, str(exc))
    return redirect(gen.get_absolute_url())


@login_required
def history(request):
    plan = entitlements.plan_for(request.user)
    visible = _visible_generated_ids(request.user)
    generated = GeneratedCV.objects.filter(pk__in=visible).select_related("request", "request__job").order_by("-created_at")
    hidden = GeneratedCV.objects.filter(user=request.user).count() - len(visible)
    failed = GenerationRequest.objects.filter(user=request.user, status__in=["failed", "queued", "processing"])[:10]
    return render(request, "cvs/history.html", {"generated": generated, "hidden": hidden, "plan": plan, "pending_or_failed": failed})


@login_required
def generated_detail(request, public_id):
    gcv = _own_generated(request, public_id)
    plan = entitlements.plan_for(request.user)
    fit = gcv.fit_analysis or {}
    if not plan.detailed_fit_analysis:
        # Server-side gating: basic plans get the summary, never the detailed evidence.
        fit = {
            "counts": fit.get("counts", {}),
            "missing": [a["requirement"] for a in fit.get("assessments", []) if a.get("support") == "missing"][:5],
        }
    return render(
        request,
        "cvs/generated_detail.html",
        {
            "gcv": gcv,
            "cv": RenderedCV.model_validate(gcv.content),
            "fit": fit,
            "detailed": plan.detailed_fit_analysis,
            "plan": plan,
            "report": gcv.validation_report or {},
            "regen_left": max(plan.section_regenerations_per_cv - gcv.section_regenerations_used, 0),
        },
    )


def _lines(text):
    return [ln.strip().lstrip("•-* ").strip() for ln in (text or "").splitlines() if ln.strip()]


@login_required
def generated_edit(request, public_id):
    gcv = _own_generated(request, public_id)
    plan = entitlements.plan_for(request.user)
    if not plan.can_edit_generated:
        messages.error(request, "Editing is not available on your plan.")
        return redirect(gcv.get_absolute_url())
    cv = RenderedCV.model_validate(gcv.content)
    if request.method == "POST":
        p = request.POST
        cv.headline = p.get("headline", cv.headline)[:200]
        cv.summary = p.get("summary", cv.summary)[:3000]
        cv.skills = [s.strip()[:60] for s in p.get("skills", "").split(",") if s.strip()][:60]
        for e in cv.experience:
            if f"exp_{e.id}" in p:
                e.bullets = _lines(p.get(f"exp_{e.id}"))[:12]
        for pr in cv.projects:
            if f"proj_{pr.id}" in p:
                pr.bullets = _lines(p.get(f"proj_{pr.id}"))[:12]
                pr.description = p.get(f"projdesc_{pr.id}", pr.description)[:1000]
        warnings = services.save_user_edit(gcv, cv)
        for w in warnings:
            messages.warning(request, w)
        messages.success(request, "Changes saved.")
        return redirect(gcv.get_absolute_url())
    return render(request, "cvs/generated_edit.html", {"gcv": gcv, "cv": cv})


@login_required
@require_POST
@ratelimit("regen", limit=20, window_seconds=3600, by="user")
def generated_regenerate(request, public_id):
    gcv = _own_generated(request, public_id)
    form = SectionRegenForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Invalid request.")
        return redirect(gcv.get_absolute_url())
    try:
        _, rejected = services.regenerate_section(gcv, form.cleaned_data["section"], form.cleaned_data["item_id"], form.cleaned_data["instruction"])
        messages.success(request, "Section regenerated." + (f" {len(rejected)} suggestion(s) were rejected by the fact check." if rejected else ""))
    except services.GenerationRejected as exc:
        messages.error(request, str(exc))
    except AIError as exc:
        messages.error(request, exc.user_message)
    return redirect(gcv.get_absolute_url())


@login_required
def generated_download(request, public_id, fmt):
    gcv = _own_generated(request, public_id)
    plan = entitlements.plan_for(request.user)
    cv = RenderedCV.model_validate(gcv.content)
    base = slugify(f"{cv.full_name or 'cv'}-{gcv.request.target_title}")[:80] or "cv"
    if fmt == "pdf":
        if not plan.can_export_pdf:
            messages.error(request, "PDF export is not included in your plan.")
            return redirect(gcv.get_absolute_url())
        data, ctype, ext = exports.render_pdf(cv), "application/pdf", "pdf"
    elif fmt == "docx":
        if not plan.can_export_docx:
            messages.error(request, "DOCX export is a Premium feature.")
            return redirect("billing:pricing")
        data, ctype, ext = exports.render_docx(cv), "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"
    elif fmt == "json":
        data, ctype, ext = json.dumps(gcv.content, indent=2, ensure_ascii=False).encode(), "application/json", "json"
    else:
        raise Http404
    resp = HttpResponse(data, content_type=ctype)
    resp["Content-Disposition"] = f'attachment; filename="{base}.{ext}"'
    resp["Cache-Control"] = "private, no-store"
    return resp


@login_required
@require_POST
def generated_delete(request, public_id):
    gcv = get_object_or_404(GeneratedCV, public_id=public_id, user=request.user)
    gcv.delete()
    messages.success(request, "Tailored CV deleted.")
    return redirect("cvs:history")
