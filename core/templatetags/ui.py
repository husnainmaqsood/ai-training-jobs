import json
from urllib.parse import urlencode

from django import template
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from core.countries import country_name as _country_name

register = template.Library()


@register.filter
def country_name(code):
    return _country_name(code)


@register.filter
def cents(value):
    try:
        amount = int(value) / 100
    except (TypeError, ValueError):
        return ""
    return f"{amount:,.0f}" if amount == int(amount) else f"{amount:,.2f}"


@register.filter
def get(d, key):
    try:
        return d.get(key)
    except AttributeError:
        return None


@register.simple_tag
def compensation(job):
    if not job.show_compensation:
        return ""
    cur = job.compensation_currency
    sym = {"USD": "$", "GBP": "£", "EUR": "€", "INR": "₹"}.get(cur, "")

    def fmt(v):
        v = float(v)
        s = f"{v:,.0f}" if v == int(v) else f"{v:,.2f}"
        return f"{sym}{s}" if sym else f"{s} {cur}"

    lo, hi = job.compensation_min, job.compensation_max
    rng = f"{fmt(lo)}–{fmt(hi)}" if lo is not None and hi is not None and lo != hi else fmt(lo if lo is not None else hi)
    if lo is None and hi is not None:
        rng = f"up to {rng}"
    return f"{rng} {job.get_compensation_period_display()}".strip()


@register.filter
def joinlines(items):
    """One item per line, e.g. for pre-filling a <textarea>. Autoescaping still applies to the result."""
    return "\n".join(str(i) for i in items or [])


@register.filter
def ago(value):
    """'3 days' — the largest unit only, for compact listings."""
    from django.utils.timesince import timesince

    return timesince(value, depth=1) if value else ""


@register.filter
def company_color(name):
    from jobs.stats import company_color as _color

    return _color(name)


@register.filter
def percent_of(part, whole):
    try:
        return round(100 * part / whole) if whole else 0
    except (TypeError, ValueError):
        return 0


@register.filter
def avatar_class(name):
    """Stable, muted colour for a company monogram."""
    return f"avatar-c{sum(ord(c) for c in (name or '').lower()) % 8}"


@register.filter
def initials(name):
    words = [w for w in (name or "").replace("-", " ").split() if w[:1].isalnum()]
    if not words:
        return "?"
    if len(words) == 1:
        return words[0][:2].capitalize()
    return (words[0][0] + words[1][0]).upper()


@register.simple_tag
def rate_label(job):
    """Structured, verified pay if available, otherwise the rate exactly as the admin wrote it."""
    return compensation(job) or job.rate_text


@register.simple_tag(takes_context=True)
def page_url(context, page_number):
    qs = context.get("querystring", "")
    return "?" + (qs + "&" if qs else "") + urlencode({"page": page_number})


@register.filter
def jsonld(data):
    """Serialize to JSON safe for embedding in <script type="application/ld+json">."""
    text = json.dumps(data, ensure_ascii=False)
    text = text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return mark_safe(text)  # noqa: S308 — <, > and & are escaped above, so this cannot break out of <script>


@register.simple_tag
def job_jsonld(job, site_url):
    if not job.is_public or job.is_demo:
        return ""
    emp = {
        "contract": "CONTRACTOR",
        "freelance": "CONTRACTOR",
        "hourly": "CONTRACTOR",
        "part_time": "PART_TIME",
        "full_time": "FULL_TIME",
        "internship": "INTERN",
    }.get(job.employment_type, "OTHER")
    data = {
        "@context": "https://schema.org/",
        "@type": "JobPosting",
        "title": job.title,
        "description": job.description.replace("\n", "<br>"),
        "datePosted": job.published_at.date().isoformat() if job.published_at else None,
        "employmentType": emp,
        "hiringOrganization": {"@type": "Organization", "name": job.display_company, "sameAs": job.platform.website},
        "directApply": False,
    }
    if job.expires_at:
        data["validThrough"] = job.expires_at.isoformat()
    if job.work_arrangement == "remote":
        data["jobLocationType"] = "TELECOMMUTE"
        if job.eligibility_scope == "restricted":
            data["applicantLocationRequirements"] = [{"@type": "Country", "name": _country_name(c)} for c in job.eligible_countries]
    if job.show_compensation and job.compensation_period in {"hour", "week", "month", "year"}:
        value = {"@type": "QuantitativeValue", "unitText": job.compensation_period.upper()}
        if job.compensation_min is not None:
            value["minValue"] = float(job.compensation_min)
        if job.compensation_max is not None:
            value["maxValue"] = float(job.compensation_max)
        data["baseSalary"] = {"@type": "MonetaryAmount", "currency": job.compensation_currency, "value": value}
    data = {k: v for k, v in data.items() if v is not None}
    if job.work_arrangement == "remote" and job.eligibility_scope != "restricted":
        return ""  # can't express worldwide/unknown eligibility accurately in JobPosting; omit rather than mislead
    return format_html('<script type="application/ld+json">{}</script>', jsonld(data))


SUPPORT_BADGE = {"supported": "badge-green", "partial": "badge-amber", "missing": "badge-red"}
STATUS_BADGE = {
    "saved": "badge-slate",
    "cv_tailored": "badge-brand",
    "prepared": "badge-brand",
    "applied": "badge-sky",
    "assessment": "badge-amber",
    "interview": "badge-amber",
    "offer": "badge-green",
    "rejected": "badge-red",
    "withdrawn": "badge-slate",
}


@register.filter
def support_badge(value):
    return SUPPORT_BADGE.get(value, "badge-slate")


@register.filter
def status_badge(value):
    return STATUS_BADGE.get(value, "badge-slate")
