"""
Directory statistics, computed from Job rows on this site.

These count *listings in this directory*, not vacancies on the companies' own platforms — templates must say so.
"""

from datetime import timedelta

from django.conf import settings
from django.db.models import Count, Q
from django.utils import timezone

from .models import Job, logo_url

VERIFIED_WITHIN_DAYS = 30

# Muted, distinguishable colours for per-company bars and pills (stable per name).
COMPANY_COLORS = ["#5856dc", "#12a37f", "#e5883a", "#e5484d", "#0ea5e9", "#a259e6", "#14b8a6", "#78716c"]


def company_color(name):
    """Colour by alphabetical position, so up to 8 companies never share a colour."""
    from .models import Platform

    return COMPANY_COLORS[Platform.objects.filter(name__lt=name or "").count() % len(COMPANY_COLORS)]


CLOSED_STATUSES = [Job.Status.EXPIRED, Job.Status.CLOSED, Job.Status.ARCHIVED]


def by_category(public):
    """Open listings per category; uncategorised ones are counted as "Other" so the rows always add up to the total."""
    rows = list(
        public.filter(category__isnull=False)
        .values("category__name", "category__slug", "category__description")
        .annotate(n=Count("id"))
        .order_by("-n", "category__name")
    )
    other = public.filter(category__isnull=True).count()
    if other:
        rows.append(
            {"category__name": "Other roles", "category__slug": "", "category__description": "Listings that don't fit a field yet.", "n": other}
        )
    return rows


def directory_stats(recent=5):
    now = timezone.now()
    public = Job.objects.public()
    total = public.count()

    by_company = list(
        public.values("platform__name", "platform__slug", "platform__logo_version").annotate(n=Count("id")).order_by("-n", "platform__name")
    )
    for row in by_company:
        row["logo"] = logo_url(row["platform__slug"], row["platform__logo_version"])
        row["pct"] = round(100 * row["n"] / total, 1) if total else 0
        row["color"] = company_color(row["platform__name"])

    verified_cutoff = now - timedelta(days=VERIFIED_WITHIN_DAYS)
    verified_open = public.filter(last_verified_at__gte=verified_cutoff).count()
    closed_qs = Job.objects.filter(Q(status__in=CLOSED_STATUSES) | Q(status=Job.Status.PUBLISHED, expires_at__lte=now) | Q(link_broken=True))
    if not getattr(settings, "SHOW_DEMO_JOBS", False):
        closed_qs = closed_qs.filter(is_demo=False)
    closed = closed_qs.count()
    return {
        "total": total,
        "companies": len(by_company),
        "by_company": by_company,
        "verified_open": verified_open,
        "unverified_open": total - verified_open,
        "closed": closed,
        "verified_pct": round(100 * verified_open / total) if total else 0,
        "verified_days": VERIFIED_WITHIN_DAYS,
        "added_7d": public.filter(created_at__gte=now - timedelta(days=7)).count(),
        "with_rate": public.filter(Q(compensation_verified=True) | ~Q(rate_text="")).count(),
        "remote": public.filter(work_arrangement=Job.WorkArrangement.REMOTE).count(),
        "titles": public.values("title").distinct().count(),
        "by_category": by_category(public),
        "recent": public.select_related("platform").order_by("-published_at", "-created_at")[:recent],
    }
