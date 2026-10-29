from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from jobs.models import Job, Platform

from .services import record_click, resolve_job_destination, resolve_platform_destination


@never_cache
@require_GET
def go_job(request, pk):
    """Outbound application redirect. Destination comes only from stored, admin-approved URLs (no open redirect)."""
    job = get_object_or_404(Job.objects.select_related("platform", "source"), pk=pk)
    if not job.is_public and not request.user.is_staff:
        return HttpResponseRedirect(job.get_absolute_url())
    url, link_type, program = resolve_job_destination(job)
    record_click(request, destination=url, link_type=link_type, job=job, program=program, page=request.GET.get("from", "")[:40])
    response = HttpResponseRedirect(url)
    response["Referrer-Policy"] = "origin"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


@never_cache
@require_GET
def go_platform(request, slug):
    platform = get_object_or_404(Platform, slug=slug, is_active=True)
    url, link_type, program = resolve_platform_destination(platform)
    record_click(request, destination=url, link_type=link_type, platform=platform, program=program, page="platform")
    response = HttpResponseRedirect(url)
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response
