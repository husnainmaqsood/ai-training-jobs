"""
CV builder: generates a CV from the user's saved profile, optionally tailored to a pasted job description.

* Facts (name, contact, employers, titles, dates, education, extra details) come straight from the profile.
  The model only rewords descriptions and writes the summary; `build_validated_cv` rebuilds the document from
  the profile facts and drops any sentence mentioning numbers or technologies the profile doesn't contain.
* Each generation costs one credit, reserved before the AI call and refunded if the call fails.
* Results are returned to the caller (kept in the user's session), not stored as files or history rows.
"""

import logging
import uuid

from django.conf import settings
from django.utils import timezone

from ai.service import AIError, AINotConfigured, ai_is_configured, check_global_limits, run_structured
from billing import credits
from jobs.models import Skill

from . import prompts
from .parsing import clean_text
from .schemas import Contact, EducationItem, ExperienceItem, OtherSection, StructuredCV, TailorOutput
from .validation import all_known_terms, build_validated_cv

logger = logging.getLogger(__name__)

MODE_GENERAL = "general"
MODE_JD = "jd"
MIN_JD_CHARS = 100


class BuilderError(Exception):
    """Message is safe to show to the user."""


def _lines(text):
    return [ln.strip().lstrip("•-*· ").strip() for ln in (text or "").splitlines() if ln.strip().lstrip("•-*· ").strip()]


def profile_problems(user):
    """What's missing before a CV can be generated (empty list when ready)."""
    problems = []
    if not user.profile.full_name.strip():
        problems.append("Add your full name.")
    if not user.work_experiences.exists():
        problems.append("Add at least one role to your work experience.")
    return problems


def profile_source(user):
    """(StructuredCV, raw_text) built only from what the user saved."""
    p = user.profile
    experience = [
        ExperienceItem(
            id=f"exp{i}",
            employer=e.company,
            title=e.title,
            location=e.location,
            start_date=e.start_date,
            end_date=e.end_display,
            bullets=_lines(e.description),
        )
        for i, e in enumerate(user.work_experiences.all(), start=1)
    ]
    education = [EducationItem(id=f"edu{i}", institution="", qualification=line) for i, line in enumerate(_lines(p.education), start=1)]
    extra = _lines(p.additional_info)
    cv = StructuredCV(
        full_name=p.full_name.strip(),
        headline=p.headline.strip(),
        contact=Contact(
            email=user.email,
            phone=p.phone.strip(),
            location=p.location.strip(),
            links=[link.strip() for link in p.links.split(",") if link.strip()],
        ),
        skills=list(p.skills),
        experience=experience,
        education=education,
        other_sections=[OtherSection(title="Additional Information", items=extra)] if extra else [],
    )
    raw = "\n".join(
        [cv.full_name, cv.headline, ", ".join(cv.skills)]
        + [f"{e.title} at {e.employer} ({e.start_date} – {e.end_date})\n" + "\n".join(e.bullets) for e in experience]
        + [e.qualification for e in education]
        + extra
    )
    return cv, raw


def _for_ai(cv: StructuredCV) -> dict:
    data = cv.model_dump()
    data.pop("contact", None)  # the model doesn't need contact details
    data.pop("full_name", None)
    return data


def generate(user, *, mode, target_title="", target_company="", job_description=""):
    """Generate a CV. Returns a dict for the session. Raises BuilderError or credits.InsufficientCredits."""
    if mode not in {MODE_GENERAL, MODE_JD}:
        raise BuilderError("Unknown CV type.")
    problems = profile_problems(user)
    if problems:
        raise BuilderError("Complete your profile first: " + " ".join(problems))
    job_description = clean_text(job_description or "")[: settings.JOB_DESCRIPTION_MAX_CHARS]
    if mode == MODE_JD and len(job_description) < MIN_JD_CHARS:
        raise BuilderError("Please paste the full job description (at least a short paragraph).")
    if not ai_is_configured():
        raise BuilderError(AINotConfigured.user_message)
    try:
        check_global_limits()
    except AIError as exc:
        raise BuilderError(exc.user_message) from exc

    source, raw_text = profile_source(user)
    vocab = list(Skill.objects.values_list("name", flat=True)[:2000])
    result_id = uuid.uuid4().hex
    txn = credits.consume(user, reference=f"cv-{result_id}", note=f"mode={mode}")  # raises InsufficientCredits

    try:
        if mode == MODE_JD:
            out = run_structured(
                purpose="tailor_cv",
                system=prompts.TAILOR_SYSTEM,
                user_content=prompts.tailor_user_message(_for_ai(source), target_title or "Not specified", target_company, job_description),
                output_model=TailorOutput,
                user=user,
            )
            rendered, fit, score, report = build_validated_cv(source, raw_text, out, job_description, vocab)
        else:
            goal = f"Target role: {target_title}" if target_title else "No specific target role."
            out = run_structured(
                purpose="build_cv",
                system=prompts.BUILD_SYSTEM,
                user_content=prompts.tailor_user_message(_for_ai(source), target_title or "General CV", "", goal),
                output_model=TailorOutput,
                user=user,
            )
            rendered, fit, score, report = build_validated_cv(source, raw_text, out, "", vocab, terms=all_known_terms(vocab))
    except AIError as exc:
        credits.refund(txn, note=f"ai:{type(exc).__name__}")
        raise BuilderError(exc.user_message.replace("quota", "credit")) from exc
    except Exception as exc:
        logger.exception("CV builder generation failed")
        credits.refund(txn, note="unexpected")
        raise BuilderError("Something went wrong while generating your CV. Your credit has been refunded.") from exc

    return {
        "id": result_id,
        "mode": mode,
        "target_title": target_title[:200],
        "target_company": target_company[:150],
        "content": rendered.model_dump(),
        "missing": [a["requirement"] for a in fit.get("assessments", []) if a.get("support") == "missing"][:8],
        "recommendations": fit.get("recommendations", [])[:5],
        "score": score if mode == MODE_JD else None,
        "rejected": len(report.get("rejected_text", [])) + len(report.get("removed_skills", [])),
        "created_at": timezone.now().isoformat(),
    }
