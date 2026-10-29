import logging
from datetime import timedelta

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from ai.service import (
    AIError,
    AINotConfigured,
    ai_is_configured,
    check_global_limits,
    run_structured,
)
from billing import entitlements
from jobs.models import Skill

from . import prompts
from .models import GeneratedCV, GenerationRequest, MasterCV
from .parsing import (
    CVParseError,
    clean_text,
    extract_contact,
    extract_text,
    heuristic_structure,
    read_upload,
    redact_for_ai,
    sha256,
)
from .schemas import ParsedCVOutput, RenderedCV, SectionRegenOutput, StructuredCV, TailorOutput
from .validation import build_validated_cv, check_text, contains_term, full_corpus, job_terms, numbers_in

logger = logging.getLogger(__name__)


class GenerationRejected(Exception):
    """Raised before any quota is consumed. Message is safe for users."""


# --- Master CVs ---------------------------------------------------------------------------


def create_master_cv_from_upload(user, title, uploaded_file, make_primary=False):
    file_type, data = read_upload(uploaded_file)
    text = extract_text(file_type, data)
    cv = MasterCV(
        user=user,
        title=title or uploaded_file.name[:150],
        file_type=file_type,
        original_filename=uploaded_file.name[:255],
        file_sha256=sha256(data),
        raw_text=text,
    )
    _apply_heuristic(cv)
    cv.original_file.save(f"upload.{file_type}", ContentFile(data), save=False)
    _save_master(cv, make_primary)
    return cv


def create_master_cv_from_text(user, title, text, make_primary=False):
    text = clean_text(text or "")
    if len(text) < 80:
        raise CVParseError("Please paste your full CV (at least a few lines).")
    cv = MasterCV(user=user, title=title or "Pasted CV", file_type=MasterCV.FileType.TEXT, raw_text=text, file_sha256=sha256(text.encode()))
    _apply_heuristic(cv)
    _save_master(cv, make_primary)
    return cv


def _apply_heuristic(cv):
    cv.structured = heuristic_structure(cv.raw_text).model_dump()
    cv.parse_status = MasterCV.ParseStatus.TEXT_ONLY
    cv.parse_error = ""


def _save_master(cv, make_primary):
    with transaction.atomic():
        has_primary = MasterCV.objects.filter(user=cv.user, is_primary=True).exists()
        if make_primary or not has_primary:
            MasterCV.objects.filter(user=cv.user, is_primary=True).update(is_primary=False)
            cv.is_primary = True
        cv.save()


def update_master_text(cv, text):
    text = clean_text(text)
    if len(text) < 80:
        raise CVParseError("CV text is too short.")
    cv.raw_text = text
    _apply_heuristic(cv)
    cv.save()


def ensure_structured(master: MasterCV, user, generation=None) -> StructuredCV:
    """Return the AI-structured CV, parsing it once and caching on the MasterCV."""
    if master.parse_status == MasterCV.ParseStatus.STRUCTURED and master.structured:
        return StructuredCV.model_validate(master.structured)
    contact = extract_contact(master.raw_text)
    out = run_structured(
        purpose="parse_cv",
        system=prompts.PARSE_SYSTEM,
        user_content=prompts.parse_user_message(redact_for_ai(master.raw_text, contact)),
        output_model=ParsedCVOutput,
        user=user,
        generation=generation,
    )
    cv = out.cv
    cv.contact.email = contact.email
    cv.contact.phone = contact.phone
    if not cv.contact.links:
        cv.contact.links = contact.links
    # Ids must be unique for the factual-preservation mapping to work.
    for prefix, items in (("exp", cv.experience), ("edu", cv.education), ("proj", cv.projects), ("cert", cv.certifications)):
        for i, item in enumerate(items, start=1):
            item.id = f"{prefix}{i}"
    master.structured = cv.model_dump()
    master.parse_status = MasterCV.ParseStatus.STRUCTURED
    master.parse_error = "; ".join(out.warnings)[:500]
    master.save(update_fields=["structured", "parse_status", "parse_error", "updated_at"])
    return cv


def structured_for_ai(cv: StructuredCV) -> dict:
    """Strip direct identifiers: the tailoring model doesn't need name or contact details."""
    data = cv.model_dump()
    data.pop("contact", None)
    data.pop("full_name", None)
    return data


# --- Generations -----------------------------------------------------------------------------


def start_generation(*, user, master: MasterCV, job=None, target_title, target_company="", job_description):
    job_description = clean_text(job_description or "")[: settings.JOB_DESCRIPTION_MAX_CHARS]
    if master.user_id != user.pk:
        raise GenerationRejected("CV not found.")
    if len(job_description) < 100:
        raise GenerationRejected("Please paste the full job description (at least a short paragraph).")
    if not ai_is_configured():
        raise GenerationRejected(AINotConfigured.user_message)
    recent = GenerationRequest.objects.filter(user=user, created_at__gte=timezone.now() - timedelta(hours=1)).count()
    if recent >= settings.AI_USER_HOURLY_REQUEST_LIMIT:
        raise GenerationRejected("You've started many generations in the last hour. Please wait a bit and try again.")
    if GenerationRequest.objects.filter(
        user=user,
        status__in=[GenerationRequest.Status.QUEUED, GenerationRequest.Status.PROCESSING],
        created_at__gte=timezone.now() - timedelta(minutes=10),
    ).exists():
        raise GenerationRejected("A generation is already in progress. Please wait for it to finish.")
    try:
        check_global_limits()
    except AIError as exc:
        raise GenerationRejected(exc.user_message) from exc

    with transaction.atomic():
        gen = GenerationRequest.objects.create(
            user=user,
            master_cv=master,
            job=job,
            target_title=target_title[:200],
            target_company=target_company[:150],
            job_description=job_description,
            progress_message="Queued",
        )
        entitlements.reserve_generation(user, gen)  # raises QuotaExceeded → whole transaction rolls back
    # Dispatched after the atomic block has committed, so the worker always sees the row.
    from .tasks import run_generation_task

    run_generation_task.delay(gen.pk)
    gen.refresh_from_db()
    return gen


def _progress(gen, msg):
    GenerationRequest.objects.filter(pk=gen.pk).update(progress_message=msg)


def _skill_vocab():
    return list(Skill.objects.values_list("name", flat=True)[:2000])


def run_generation(generation_id):
    with transaction.atomic():
        gen = GenerationRequest.objects.select_for_update().select_related("master_cv", "user").get(pk=generation_id)
        if gen.status != GenerationRequest.Status.QUEUED:
            return gen  # idempotent: already running/finished (e.g. duplicate task delivery)
        gen.status = GenerationRequest.Status.PROCESSING
        gen.started_at = timezone.now()
        gen.attempts += 1
        gen.progress_message = "Reading your CV"
        gen.save()

    try:
        source = ensure_structured(gen.master_cv, gen.user, gen)
        _progress(gen, "Analysing the job and tailoring your CV")
        out = run_structured(
            purpose="tailor_cv",
            system=prompts.TAILOR_SYSTEM,
            user_content=prompts.tailor_user_message(structured_for_ai(source), gen.target_title, gen.target_company, gen.job_description),
            output_model=TailorOutput,
            user=gen.user,
            generation=gen,
        )
        _progress(gen, "Checking facts against your original CV")
        rendered, fit, score, report = build_validated_cv(source, gen.master_cv.raw_text, out, gen.job_description, _skill_vocab())
        with transaction.atomic():
            GeneratedCV.objects.create(
                user=gen.user,
                request=gen,
                version=1,
                title=f"{gen.target_title}" + (f" — {gen.target_company}" if gen.target_company else ""),
                content=rendered.model_dump(),
                fit_analysis=fit,
                match_score=score,
                validation_report=report,
            )
            gen.status = GenerationRequest.Status.SUCCEEDED
            gen.progress_message = "Done"
            gen.finished_at = timezone.now()
            gen.save()
            if gen.job_id:
                from tracker.services import mark_cv_tailored

                mark_cv_tailored(gen.user, gen.job, gen.versions.first())
    except AIError as exc:
        _fail(gen, exc.user_message, f"ai:{type(exc).__name__}")
    except Exception:
        logger.exception("Generation %s failed unexpectedly", gen.pk)
        _fail(gen, "Something went wrong while tailoring your CV. Your quota has been refunded.", "unexpected")
    gen.refresh_from_db()
    return gen


def _fail(gen, user_message, reason):
    GenerationRequest.objects.filter(pk=gen.pk).update(
        status=GenerationRequest.Status.FAILED,
        error_message=user_message[:500],
        progress_message="Failed",
        finished_at=timezone.now(),
    )
    entitlements.refund_generation(gen, reason)


# --- Section regeneration & editing -----------------------------------------------------------

REGEN_SECTIONS = ("summary", "skills", "experience", "project")


def regenerate_section(gcv: GeneratedCV, section: str, item_id: str = "", instruction: str = ""):
    """Rewrite one section. Limited per CV by plan; does not consume generation quota."""
    plan = entitlements.plan_for(gcv.user)
    if section not in REGEN_SECTIONS:
        raise GenerationRejected("Unknown section.")
    gen = gcv.request
    source = StructuredCV.model_validate(gen.master_cv.structured)
    rendered = RenderedCV.model_validate(gcv.content)

    if section == "summary":
        original = {
            "summary": source.summary,
            "headline": source.headline,
            "skills": source.skills,
            "experience_titles": [f"{e.title} @ {e.employer}" for e in source.experience],
        }
        source_text = full_corpus(source, gen.master_cv.raw_text)
    elif section == "skills":
        original = {"skills": source.skills}
        source_text = full_corpus(source, gen.master_cv.raw_text)
    elif section == "experience":
        item = next((e for e in source.experience if e.id == item_id), None)
        if item is None:
            raise GenerationRejected("Experience entry not found.")
        original = {"title": item.title, "employer": item.employer, "bullets": item.bullets}
        source_text = "\n".join([item.title, item.employer, *item.bullets])
    else:
        item = next((p for p in source.projects if p.id == item_id), None)
        if item is None:
            raise GenerationRejected("Project not found.")
        original = {"name": item.name, "description": item.description, "bullets": item.bullets, "technologies": item.technologies}
        source_text = "\n".join([item.name, item.description, *item.technologies, *item.bullets])

    with transaction.atomic():
        locked = GeneratedCV.objects.select_for_update().get(pk=gcv.pk)
        if locked.section_regenerations_used >= plan.section_regenerations_per_cv:
            raise GenerationRejected(
                "You've used all section regenerations for this CV on your plan."
                if plan.section_regenerations_per_cv
                else "Section regeneration is a Premium feature."
            )
        locked.section_regenerations_used += 1
        locked.save(update_fields=["section_regenerations_used"])

    try:
        out = run_structured(
            purpose="regenerate_section",
            system=prompts.SECTION_SYSTEM,
            user_content=prompts.section_user_message(section, original, gen.target_title, gen.job_description, instruction),
            output_model=SectionRegenOutput,
            user=gcv.user,
            generation=gen,
            max_output_tokens=1500,
        )
    except AIError:
        # provider failure: give the regeneration back
        GeneratedCV.objects.filter(pk=gcv.pk, section_regenerations_used__gt=0).update(section_regenerations_used=F("section_regenerations_used") - 1)
        raise

    terms = job_terms(gen.job_description, _skill_vocab())
    allowed = numbers_in(source_text)
    rejected = []
    if section == "summary":
        text = out.summary.strip()
        problems = check_text(text, source_text, terms, allowed)
        if text and not problems:
            rendered.summary = text
        else:
            rejected.append({"text": text, "reasons": problems or ["empty"]})
    elif section == "skills":
        corpus = full_corpus(source, gen.master_cv.raw_text)
        good = [s for s in out.skills if contains_term(s, corpus)]
        rejected += [{"text": s, "reasons": ["not evidenced in your CV"]} for s in out.skills if s not in good]
        rendered.skills = list(dict.fromkeys(good + [s for s in rendered.skills if s not in good]))
    else:
        bullets = []
        for b in out.bullets[:8]:
            problems = check_text(b, source_text, terms, allowed)
            (rejected.append({"text": b, "reasons": problems}) if problems else bullets.append(b.strip()))
        target_list = rendered.experience if section == "experience" else rendered.projects
        for entry in target_list:
            if entry.id == item_id and bullets:
                entry.bullets = bullets
                if section == "project" and out.description and not check_text(out.description, source_text, terms, allowed):
                    entry.description = out.description.strip()

    report = dict(gcv.validation_report or {})
    report.setdefault("section_regenerations", []).append(
        {"section": section, "item": item_id, "rejected": rejected, "at": timezone.now().isoformat()}
    )
    GeneratedCV.objects.filter(pk=gcv.pk).update(content=rendered.model_dump(), validation_report=report, updated_at=timezone.now())
    gcv.refresh_from_db()
    return gcv, rejected


def save_user_edit(gcv: GeneratedCV, rendered: RenderedCV):
    from .validation import validate_rendered_edit

    source = StructuredCV.model_validate(gcv.request.master_cv.structured)
    warnings = validate_rendered_edit(source, gcv.request.master_cv.raw_text, rendered)
    report = dict(gcv.validation_report or {})
    report["edit_warnings"] = warnings
    gcv.content = rendered.model_dump()
    gcv.is_user_edited = True
    gcv.validation_report = report
    gcv.save()
    return warnings
