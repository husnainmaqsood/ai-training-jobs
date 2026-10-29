from django.contrib.postgres.search import SearchQuery, SearchRank
from django.db.models import F, Q

from .models import Job


def filter_jobs(qs, data):
    """Apply validated JobFilterForm data to a Job queryset. Returns (queryset, used_search)."""
    q = (data.get("q") or "").strip()
    used_search = False
    if q:
        query = SearchQuery(q, search_type="websearch", config="english")
        qs = qs.filter(Q(search_vector=query) | Q(title__icontains=q)).annotate(rank=SearchRank(F("search_vector"), query))
        used_search = True
    if data.get("country"):
        qs = qs.eligible_for_country(data["country"])
    if data.get("category"):
        qs = qs.filter(category__slug=data["category"])
    if data.get("skill"):
        qs = qs.filter(skills__slug=data["skill"])
    if data.get("platform"):
        qs = qs.filter(platform__slug=data["platform"])
    if data.get("employment_type"):
        qs = qs.filter(employment_type=data["employment_type"])
    if data.get("experience"):
        qs = qs.filter(experience_level=data["experience"])
    if data.get("remote_only"):
        qs = qs.filter(work_arrangement=Job.WorkArrangement.REMOTE)
    if data.get("worldwide_only"):
        qs = qs.filter(eligibility_scope=Job.Eligibility.WORLDWIDE)
    if data.get("min_hourly") is not None:
        qs = qs.filter(compensation_hourly_usd__gte=data["min_hourly"])

    sort = data.get("sort") or ("relevance" if used_search else "newest")
    if sort == "relevance" and used_search:
        qs = qs.order_by("-rank", "-published_at")
    elif sort == "pay":
        qs = qs.order_by(F("compensation_hourly_usd").desc(nulls_last=True), "-published_at")
    else:
        qs = qs.order_by("-published_at", "-pk")
    return qs.distinct(), used_search
