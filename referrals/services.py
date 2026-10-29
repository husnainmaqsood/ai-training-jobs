import hashlib

from django.conf import settings
from django.utils import timezone

from core.ratelimit import client_ip

from .models import ClickEvent, ReferralProgram


def active_program(platform):
    return ReferralProgram.objects.filter(platform=platform, status=ReferralProgram.Status.ACTIVE).first()


def resolve_job_destination(job):
    """Return (url, link_type, program). Referral links are used only with an ACTIVE, approved program."""
    program = active_program(job.platform)
    if program and program.is_usable:
        if job.referral_url:
            return job.referral_url, ClickEvent.LinkType.REFERRAL, program
        if program.tracking_template:
            url = program.build_url_for(job.application_url)
            if url:
                return url, ClickEvent.LinkType.REFERRAL, program
    return job.application_url, ClickEvent.LinkType.APPLICATION, None


def job_uses_referral(job):
    return resolve_job_destination(job)[1] == ClickEvent.LinkType.REFERRAL


def resolve_platform_destination(platform):
    program = active_program(platform)
    if program and program.is_usable and program.approved_referral_url:
        return program.approved_referral_url, ClickEvent.LinkType.REFERRAL, program
    return platform.website, ClickEvent.LinkType.PLATFORM, None


def visitor_hash(request):
    """Daily-rotating salted hash. Lets us count unique clicks without storing IP or user agent."""
    day = timezone.now().strftime("%Y-%m-%d")
    ua = request.META.get("HTTP_USER_AGENT", "")[:200]
    raw = f"{settings.CLICK_HASH_SALT}|{day}|{client_ip(request)}|{ua}"
    return hashlib.sha256(raw.encode()).hexdigest()


def record_click(request, *, destination, link_type, job=None, platform=None, program=None, page=""):
    return ClickEvent.objects.create(
        job=job,
        platform=platform or (job.platform if job else None),
        program=program,
        source=job.source if job else None,
        link_type=link_type,
        destination=destination,
        visitor_hash=visitor_hash(request),
        is_authenticated=request.user.is_authenticated,
        page=page[:40],
    )
